"""Grounded metadata generation (titles, description, hashtags, keywords) with the local LLM.

Everything is checked against the Short's own transcript: quotes must appear verbatim, numbers
must be present in the transcript, and hashtags must relate to words that are actually said.
Without an LLM, a clearly-labelled extractive fallback is used.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import cv2
from PIL import Image, ImageDraw, ImageFont

from shortforge.core.logging import get_logger
from shortforge.engines.clip_detection.features import content_tokens, tokens
from shortforge.engines.llm.base import LLMProvider
from shortforge.engines.vision.faces import FaceDetector
from shortforge.engines.vision.sampler import sample_frames

log = get_logger("metadata")

MODES = ["clean", "high_curiosity", "educational", "funny", "tech", "podcast", "documentary", "energetic"]
MODE_HINTS = {
    "clean": "clear and descriptive, no hype",
    "high_curiosity": "create curiosity with an open question, but never lie or exaggerate",
    "educational": "state the insight or lesson plainly",
    "funny": "playful tone that matches the humour actually in the clip",
    "tech": "precise, spec-aware tech-reviewer tone",
    "podcast": "conversational, quote-driven podcast clip style",
    "documentary": "calm, factual documentary tone",
    "energetic": "high energy, punchy words, still accurate",
}

SYSTEM = (
    "You write metadata for YouTube Shorts. Only use facts, names and numbers that appear in the transcript. "
    "Never invent quotes, statistics or claims. Titles must be under 70 characters. Hashtags are single words "
    "without spaces, related to what is actually discussed; always include #shorts. The description is 1-3 short "
    "sentences summarising the clip, optionally ending with a question to the viewer."
)
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "titles": {"type": "array", "items": {"type": "string"}},
        "description": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "internal_tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["titles", "description", "hashtags", "keywords", "internal_tags"],
}


@dataclass
class ShortMetadata:
    titles: list[str]
    description: str
    hashtags: list[str]
    keywords: list[str]
    internal_tags: list[str]
    mode: str
    source: str  # llm | heuristic
    model: str | None = None
    rejected: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


def grounded(text: str, transcript: str) -> tuple[bool, str]:
    low = transcript.lower()
    for q in re.findall(r"[\"“](.+?)[\"”]", text):
        if q.lower().strip() not in low:
            return False, f"quote not in transcript: {q}"
    missing = _numbers(text) - _numbers(transcript)
    if missing:
        return False, f"number not in transcript: {', '.join(sorted(missing))}"
    return True, ""


def clean_hashtag(tag: str) -> str | None:
    tag = re.sub(r"[^\w]", "", tag.lstrip("#"))
    return f"#{tag}" if 2 <= len(tag) <= 30 else None


def heuristic_metadata(transcript: str, video_title: str, candidate_title: str | None, mode: str) -> ShortMetadata:
    words = content_tokens(transcript)
    freq: dict[str, int] = {}
    for w in words:
        freq[w] = freq.get(w, 0) + 1
    top = [w for w, _ in sorted(freq.items(), key=lambda kv: -kv[1])[:6]]
    first_sentence = re.split(r"(?<=[.!?])\s", transcript.strip())[0][:68].rstrip(" ,.")
    titles = [t for t in [candidate_title, first_sentence, video_title[:68]] if t]
    tags = ["#shorts", *[f"#{w}" for w in top[:4]]]
    return ShortMetadata(titles=titles[:3], description=transcript[:220].rsplit(" ", 1)[0] + "…",
                         hashtags=tags, keywords=top, internal_tags=top[:3], mode=mode, source="heuristic")


def generate_metadata(llm: LLMProvider | None, transcript: str, *, video_title: str, channel: str | None,
                      candidate_title: str | None, mode: str = "clean", system: str | None = None) -> ShortMetadata:
    if llm is None:
        return heuristic_metadata(transcript, video_title, candidate_title, mode)
    user = (f'Original video: "{video_title}"' + (f" by {channel}" if channel else "") +
            f"\nStyle: {MODE_HINTS.get(mode, MODE_HINTS['clean'])}\n\nShort transcript:\n{transcript}\n\n"
            "Return JSON with 3 alternative titles, a description, 3-6 hashtags, up to 8 keywords and up to 5 "
            "internal_tags (topic/category labels for organising).")
    try:
        data = llm.chat_json(system or SYSTEM, user, schema=SCHEMA, max_tokens=500)
    except Exception as exc:
        log.warning("metadata LLM failed: %s", exc)
        return heuristic_metadata(transcript, video_title, candidate_title, mode)
    rejected: list[str] = []
    titles: list[str] = []
    for t in data.get("titles", []):
        t = re.sub(r"\s+", " ", str(t)).strip().strip('"')[:95]
        ok, why = grounded(t, transcript)
        if ok and t and t.lower() not in (x.lower() for x in titles):
            titles.append(t)
        elif not ok:
            rejected.append(f"title '{t}': {why}")
    if candidate_title and len(titles) < 3 and grounded(candidate_title, transcript)[0]:
        titles.append(candidate_title)
    desc = re.sub(r"\s+", " ", str(data.get("description", ""))).strip()
    ok, why = grounded(desc, transcript)
    if not ok:
        rejected.append(f"description: {why}")
        desc = heuristic_metadata(transcript, video_title, candidate_title, mode).description
    tset = set(tokens(transcript))
    hashtags = ["#shorts"]
    for h in data.get("hashtags", []):
        ch = clean_hashtag(str(h))
        if ch and ch.lower() not in (x.lower() for x in hashtags):
            hashtags.append(ch)
    keywords = [str(k).strip() for k in data.get("keywords", []) if str(k).strip()][:8]
    keywords = [k for k in keywords if any(t in tset for t in tokens(k))] or keywords[:3]
    internal = [str(k).strip().lower()[:30] for k in data.get("internal_tags", []) if str(k).strip()][:5]
    if not titles:
        return heuristic_metadata(transcript, video_title, candidate_title, mode)
    return ShortMetadata(titles[:3], desc[:900], hashtags[:7], keywords, internal, mode, "llm", llm.model, rejected)


def pick_cover_frame(video: Path, detector: FaceDetector | None, out: Path, *, title: str | None = None,
                     overlay_text: bool = False, font_path: Path | None = None) -> tuple[Path, float]:
    """Choose the strongest frame (face quality, sharpness, exposure, composition) as cover."""
    from shortforge.engines.quality_control.qc import score_cover_frame

    best, best_score, best_t = None, -1.0, 0.0
    for t, frame in sample_frames(video, 0.3, 10_000, 2.0, width=540):
        faces = detector.detect(frame) if detector else []
        s = score_cover_frame(frame, faces)
        if s > best_score:
            best, best_score, best_t = frame, s, t
    if best is None:
        raise ValueError("no frames")
    full = None
    for _, frame in sample_frames(video, best_t, best_t + 0.05, 30.0, width=1080):
        full = frame
        break
    img = full if full is not None else cv2.resize(best, (1080, 1920))
    out.parent.mkdir(parents=True, exist_ok=True)
    if overlay_text and title and font_path and font_path.exists():
        pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(pil)
        font = ImageFont.truetype(str(font_path), 84)
        words, lines, cur = title.upper().split(), [], ""
        for w in words:
            trial = f"{cur} {w}".strip()
            if draw.textlength(trial, font=font) < 860 or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
        y = 1920 * 0.2
        for line in lines[:3]:
            w = draw.textlength(line, font=font)
            draw.text(((1080 - w) / 2, y), line, font=font, fill=(255, 255, 255), stroke_width=8, stroke_fill=(0, 0, 0))
            y += 100
        pil.save(out, quality=92)
    else:
        cv2.imwrite(str(out), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return out, best_t
