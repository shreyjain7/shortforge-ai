"""Multi-pass clip finder (PASS 3-8). Database-agnostic: takes analysis artefacts, returns candidates.

PASS 1 (transcript + audio) and PASS 2 (scenes) are produced by earlier pipeline stages.
PASS 3  candidate generation + heuristic scoring over sentence spans
PASS 4  local-LLM semantic ranking of the strongest diverse candidates
PASS 5  targeted visual analysis (faces, motion, screen content) of promising candidates
PASS 6  boundary refinement (word gaps, scene cuts, LLM trims, hook optimiser)
PASS 7  duplicate filtering (range, transcript, embeddings)
PASS 8  final ranking
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from shortforge.core.errors import JobCancelled, ShortForgeError
from shortforge.core.logging import get_logger
from shortforge.engines.audio.analysis import AudioFeatures
from shortforge.engines.clip_detection import llm_ranker
from shortforge.engines.clip_detection.action import generate_action_candidates, speech_ratio
from shortforge.engines.clip_detection.boundaries import BoundaryConfig, apply_llm_adjustments, refine
from shortforge.engines.clip_detection.candidates import (
    Candidate,
    GenerationConfig,
    TimelineContext,
    generate_candidates,
    score_span,
    select_diverse,
)
from shortforge.engines.clip_detection.dedupe import ExistingClip, find_duplicate
from shortforge.engines.clip_detection.features import (
    DocumentStats,
    SentenceFeatures,
    clamp,
    sentence_features,
)
from shortforge.engines.clip_detection.hook import analyze_hook
from shortforge.engines.llm.base import LLMProvider
from shortforge.engines.media.ffmpeg import CancelToken
from shortforge.engines.ranking.weights import combine
from shortforge.engines.transcription.types import Sentence, Word, words_text
from shortforge.engines.vision.analysis import analyze_range
from shortforge.engines.vision.faces import FaceDetector

log = get_logger("clip_finder")

ProgressFn = Callable[[float, str], None]


@dataclass
class FinderConfig:
    min_duration: float = 15.0
    max_duration: float = 60.0
    target_duration: float = 35.0
    llm_candidates: int = 16
    vision_candidates: int = 10
    final_count: int = 12
    duplicate_threshold: float = 0.8
    weights: dict[str, float] | None = None
    ranker_prompt: str | None = None


@dataclass
class FinderInputs:
    video_id: int | None
    video_title: str
    channel_name: str | None
    duration: float
    words: list[Word]
    sentences: list[Sentence]
    audio: AudioFeatures | None
    cuts: list[float]
    activity: np.ndarray | None
    timeline: TimelineContext
    proxy_path: Path | None
    existing: list[ExistingClip] = field(default_factory=list)


@dataclass
class FinderResult:
    candidates: list[Candidate]
    considered: int
    llm_used: str | None
    vision_backend: str | None
    passes: dict[str, Any]


def _blend(heur: dict[str, float], llm: dict[str, Any] | None, visual_interest: float | None) -> dict[str, float]:
    merged = dict(heur)
    if llm:
        for m in llm_ranker.LLM_METRICS:
            if m in llm and m in merged:
                merged[m] = round(0.35 * merged[m] + 0.65 * float(llm[m]), 2)
    if visual_interest is not None:
        merged["visual_activity"] = round(0.4 * merged.get("visual_activity", 50) + 0.6 * visual_interest, 2)
    return merged


def find_clips(inp: FinderInputs, cfg: FinderConfig, *, llm: LLMProvider | None = None,
               face_detector: FaceDetector | None = None, progress: ProgressFn | None = None,
               cancel: CancelToken | None = None,
               embed: Callable[[list[str]], list[list[float]] | None] | None = None,
               captioner: Callable[[bytes], str | None] | None = None) -> FinderResult:
    def report(p: float, msg: str) -> None:
        if progress:
            progress(p, msg)
        if cancel:
            cancel.raise_if_cancelled()

    if inp.duration < cfg.min_duration:
        raise ShortForgeError("This video is shorter than the minimum clip length.")
    if len(inp.sentences) < 3 or speech_ratio(inp.audio, inp.duration) < ACTION_SPEECH_THRESHOLD:
        return find_action_clips(inp, cfg, llm=llm, face_detector=face_detector, progress=progress, cancel=cancel,
                                 captioner=captioner)

    passes: dict[str, Any] = {}
    feats: list[SentenceFeatures] = sentence_features(inp.sentences, inp.words)
    doc = DocumentStats(feats)
    gen_cfg = GenerationConfig(cfg.min_duration, cfg.max_duration, cfg.target_duration)

    # ---------------- PASS 3
    report(0.02, "PASS 3 - generating candidates")
    all_cands = generate_candidates(inp.sentences, feats, inp.audio, inp.activity, inp.cuts, gen_cfg, inp.timeline,
                                    cfg.weights)
    if not all_cands:
        # Very short videos: fall back to the whole transcript span.
        last = len(inp.sentences) - 1
        m, p, b = score_span(0, last, inp.sentences, feats, inp.audio, inp.activity, inp.cuts, doc, gen_cfg,
                             inp.timeline, cfg.weights)
        all_cands = [Candidate(0, last, inp.sentences[0].start, inp.sentences[last].end,
                               " ".join(s.text for s in inp.sentences), inp.sentences[0].first_word,
                               inp.sentences[last].last_word, m, p, b.final, final=b.final, breakdown=b)]
    shortlist = select_diverse(all_cands, top_k=max(cfg.llm_candidates, cfg.final_count))
    passes["pass3"] = {"generated": len(all_cands), "shortlisted": len(shortlist)}
    log.info("PASS 3: %d spans scored, %d shortlisted", len(all_cands), len(shortlist))

    # ---------------- PASS 4
    llm_used = None
    if llm is not None:
        llm_used = llm.model
        for i, c in enumerate(shortlist[: cfg.llm_candidates]):
            report(0.1 + 0.5 * i / max(1, min(len(shortlist), cfg.llm_candidates)),
                   f"PASS 4 - semantic ranking ({i + 1}/{min(len(shortlist), cfg.llm_candidates)})")
            try:
                c.llm = llm_ranker.evaluate(llm, c, inp.sentences, inp.video_title, inp.channel_name,
                                            cfg.ranker_prompt or None)
                c.pass_reached = 4
            except JobCancelled:
                raise
            except Exception as exc:
                log.warning("LLM evaluation failed for candidate %.1f-%.1f: %s", c.start, c.end, exc)
                c.notes.append("LLM evaluation failed; heuristic score used")
        passes["pass4"] = {"model": llm.model, "evaluated": sum(1 for c in shortlist if c.llm)}
    else:
        passes["pass4"] = {"model": None, "skipped": "no local LLM available"}

    # Re-rank with LLM input before spending vision time.
    for c in shortlist:
        merged = _blend(c.metrics, c.llm, None)
        pen = dict(c.penalties)
        if c.llm and c.llm["standalone"] < 40:
            pen["missing_context"] = max(pen.get("missing_context", 0.0), 6.0)
        c.breakdown = combine(merged, pen, cfg.weights)
        c.final = c.breakdown.final
    shortlist.sort(key=lambda c: c.final, reverse=True)

    # ---------------- PASS 6 (boundaries) is applied before vision so vision sees the final range.
    bcfg = BoundaryConfig(min_duration=max(5.0, cfg.min_duration * 0.8), max_duration=cfg.max_duration * 1.15)
    refined: list[Candidate] = []
    for c in shortlist:
        first, last = c.first_sentence, c.last_sentence
        if c.llm:
            first, last = apply_llm_adjustments(c, inp.sentences, c.llm, bcfg)
        hook = analyze_hook(first, last, inp.sentences[first].start, inp.sentences, feats, inp.words, inp.audio,
                            bcfg.min_duration)
        if hook.suggested_shift and not (c.llm and c.llm.get("trim_start")):
            first += hook.suggested_shift
            hook = analyze_hook(first, last, inp.sentences[first].start, inp.sentences, feats, inp.words,
                                inp.audio, bcfg.min_duration)
        start, end, fw, lw = refine(first, last, inp.sentences, inp.words, inp.cuts, inp.duration, bcfg)
        if (first, last) != (c.first_sentence, c.last_sentence):
            m, p, _ = score_span(first, last, inp.sentences, feats, inp.audio, inp.activity, inp.cuts, doc, gen_cfg,
                                 inp.timeline, cfg.weights)
            c.metrics, c.penalties = m, p
            c.notes.append(f"boundaries adjusted to sentences {first}-{last}")
        c.first_sentence, c.last_sentence = first, last
        c.start, c.end, c.first_word, c.last_word = start, end, fw, lw
        c.text = words_text(inp.words[fw : lw + 1])
        c.metrics["hook"] = round(clamp(0.6 * c.metrics["hook"] + 0.4 * (0.3 * hook.window_1s + 0.4 * hook.window_3s
                                                                         + 0.3 * hook.window_5s)), 2)
        c.vision = {"hook": hook.to_dict()}
        refined.append(c)
    passes["pass6"] = {"refined": len(refined)}

    # ---------------- PASS 5 targeted vision
    vision_backend = None
    if face_detector is not None and inp.proxy_path is not None and inp.proxy_path.exists():
        vision_backend = face_detector.backend
        top = refined[: cfg.vision_candidates]
        for i, c in enumerate(top):
            report(0.62 + 0.25 * i / max(1, len(top)), f"PASS 5 - visual analysis ({i + 1}/{len(top)})")
            try:
                vis = analyze_range(inp.proxy_path, c.start, c.end, face_detector, fps=2.0,
                                    cuts=[x for x in inp.cuts if c.start < x < c.end])
                c.vision = {**(c.vision or {}), **vis.summary(), "visual_interest": round(vis.visual_interest(), 1),
                            "detections": vis.detections}
                c.pass_reached = max(c.pass_reached, 5)
            except JobCancelled:
                raise
            except Exception as exc:
                log.warning("visual analysis failed for %.1f-%.1f: %s", c.start, c.end, exc)
        passes["pass5"] = {"backend": vision_backend, "analyzed": len(top)}
    else:
        passes["pass5"] = {"skipped": "no proxy or face detector"}

    for c in refined:
        vi = (c.vision or {}).get("visual_interest")
        merged = _blend(c.metrics, c.llm, vi)
        pen = dict(c.penalties)
        if c.llm and c.llm["standalone"] < 40:
            pen["missing_context"] = max(pen.get("missing_context", 0.0), 6.0)
        c.breakdown = combine(merged, pen, cfg.weights)
        c.final = c.breakdown.final
        c.pass_reached = max(c.pass_reached, 6)

    # ---------------- PASS 7 duplicates
    report(0.9, "PASS 7 - duplicate filtering")
    refined.sort(key=lambda c: c.final, reverse=True)
    embeddings: list[list[float]] | None = None
    if embed is not None:
        try:
            embeddings = embed([c.text for c in refined] + [e.text for e in inp.existing if e.embedding is None])
        except Exception as exc:
            log.info("embeddings unavailable: %s", exc)
            embeddings = None
    if embeddings:
        extra = embeddings[len(refined):]
        k = 0
        for e in inp.existing:
            if e.embedding is None and k < len(extra):
                e.embedding = extra[k]
                k += 1
    accepted: list[ExistingClip] = list(inp.existing)
    kept: list[Candidate] = []
    dup_count = 0
    for idx, c in enumerate(refined):
        emb = embeddings[idx] if embeddings else None
        match = find_duplicate(inp.video_id, c.start, c.end, c.text, accepted, cfg.duplicate_threshold, emb)
        if match:
            c.duplicate_of = int(match.key.split(":")[1]) if match.key.startswith(("short:", "candidate:")) else None
            c.similarity = match.similarity
            c.notes.append(f"{match.similarity:.0%} similar to {match.key.replace(':', ' #')} ({match.reason})")
            c.penalties["repeated_topic"] = 25.0
            c.final = max(0.0, c.final - 25.0)
            dup_count += 1
        else:
            accepted.append(ExistingClip(f"new:{idx}", inp.video_id, c.start, c.end, c.text, emb))
        c.pass_reached = max(c.pass_reached, 7)
        kept.append(c)
    passes["pass7"] = {"duplicates": dup_count}

    # ---------------- PASS 8 final ranking
    kept.sort(key=lambda c: (c.duplicate_of is None, c.final), reverse=True)
    final = kept[: cfg.final_count]
    for c in final:
        c.pass_reached = 8
    passes["pass8"] = {"final": len(final)}
    report(1.0, f"Found {len(final)} candidate clips")
    return FinderResult(final, len(all_cands), llm_used, vision_backend, passes)


# ============================================================================ low-speech (action) mode
ACTION_SPEECH_THRESHOLD = 0.3

TITLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"title": {"type": "string"}, "keywords": {"type": "array", "items": {"type": "string"}},
                   "category": {"type": "string"}},
    "required": ["title", "keywords", "category"],
}


def _frame_jpeg(video: Path, t: float) -> bytes | None:
    import subprocess

    from shortforge.engines.media import ffmpeg as ff

    cmd = [ff.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, t):.2f}", "-i", str(video),
           "-frames:v", "1", "-vf", "scale=672:-2", "-q:v", "3", "-f", "image2", "-c:v", "mjpeg", "pipe:1"]
    try:
        return subprocess.run(cmd, capture_output=True, timeout=60, creationflags=ff.CREATE_NO_WINDOW).stdout or None
    except Exception:
        return None


def find_action_clips(inp: FinderInputs, cfg: FinderConfig, *, llm: LLMProvider | None,
                      face_detector: FaceDetector | None, progress: ProgressFn | None, cancel: CancelToken | None,
                      captioner: Callable[[bytes], str | None] | None) -> FinderResult:
    """Wordless footage: rank scene-aligned windows by motion, sound and rhythm; describe them visually."""
    def report(p: float, msg: str) -> None:
        if progress:
            progress(p, msg)
        if cancel:
            cancel.raise_if_cancelled()

    report(0.05, "Low speech detected - action mode: scoring motion, sound and editing rhythm")
    gen_cfg = GenerationConfig(cfg.min_duration, min(cfg.max_duration, 45.0), cfg.target_duration)
    cands = generate_action_candidates(inp.duration, inp.cuts, inp.activity, inp.audio, inp.words, gen_cfg,
                                       inp.timeline)
    if not cands:
        raise ShortForgeError("No usable segments were found in this video.")
    shortlist = select_diverse(cands, top_k=cfg.final_count, max_overlap=0.3)
    passes: dict[str, Any] = {"mode": "action", "pass3": {"generated": len(cands), "shortlisted": len(shortlist)}}
    video = inp.proxy_path if inp.proxy_path and inp.proxy_path.exists() else None
    top = shortlist[: min(len(shortlist), cfg.vision_candidates)]
    described = 0
    for i, c in enumerate(top):
        report(0.2 + 0.7 * i / max(1, len(top)), f"Describing clip {i + 1}/{len(top)} visually")
        descriptions: list[str] = []
        if captioner is not None and video is not None:
            for frac in (0.3, 0.7):
                jpeg = _frame_jpeg(video, c.start + (c.end - c.start) * frac)
                text = captioner(jpeg) if jpeg else None
                if text:
                    descriptions.append(text.strip())
        c.vision = {"descriptions": descriptions}
        if descriptions:
            described += 1
            c.llm = {"title": None, "hook_text": None, "keywords": [], "hook_type": "none", "category": "other",
                     "reason": " / ".join(descriptions)}
            if llm is not None:
                try:
                    data = llm.chat_json(
                        "You write short, accurate YouTube Shorts titles for wordless action footage. Use only what "
                        "the frame descriptions and the original video title support. No clickbait lies.",
                        f"Original video: {json.dumps(inp.video_title)}\nFrame descriptions: {json.dumps(descriptions)}\n"
                        "Return JSON: title (under 60 characters), up to 5 keywords, category (one word).",
                        schema=TITLE_SCHEMA, max_tokens=200)
                    grounding = " ".join(descriptions) + " " + inp.video_title
                    c.llm["title"] = llm_ranker.sanitize_title(str(data.get("title", "")), grounding) or None
                    c.llm["keywords"] = [str(k) for k in data.get("keywords", [])][:5]
                    c.llm["category"] = str(data.get("category", "other"))[:20]
                    c.llm["model"] = llm.model
                except Exception as exc:
                    log.info("action title generation failed: %s", exc)
    for c in shortlist:
        c.pass_reached = 8
    passes["pass5"] = {"described": described, "vision_model": captioner is not None}
    report(0.95, "PASS 7 - duplicate filtering")
    kept: list[Candidate] = []
    accepted = list(inp.existing)
    for idx, c in enumerate(sorted(shortlist, key=lambda x: x.final, reverse=True)):
        match = find_duplicate(inp.video_id, c.start, c.end, c.text, accepted, cfg.duplicate_threshold)
        if match:
            c.duplicate_of = int(match.key.split(":")[1]) if match.key.startswith(("short:", "candidate:")) else None
            c.similarity = match.similarity
            c.final = max(0.0, c.final - 25.0)
        else:
            accepted.append(ExistingClip(f"new:{idx}", inp.video_id, c.start, c.end, c.text))
        kept.append(c)
    kept.sort(key=lambda c: (c.duplicate_of is None, c.final), reverse=True)
    report(1.0, f"Found {len(kept)} action clips")
    return FinderResult(kept, len(cands), llm.model if llm else None, "motion+audio", passes)
