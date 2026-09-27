"""PASS 3 - sentence-aligned candidate generation with fast heuristic scoring.

Candidates are *natural spans of sentences*, not fixed windows: every candidate starts at a
sentence start and ends at a sentence end, so no clip ever begins or ends mid-word. Each span is
scored with cheap transcript/audio/scene features; only the strongest, mutually diverse spans are
promoted to the expensive LLM and vision passes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from shortforge.engines.audio.analysis import AudioFeatures
from shortforge.engines.clip_detection.features import (
    DocumentStats,
    SentenceFeatures,
    clamp,
    sigmoid01,
)
from shortforge.engines.ranking.weights import ScoreBreakdown, combine
from shortforge.engines.transcription.types import Sentence


@dataclass
class TimelineContext:
    """Semantic timeline labels (from the analysis stage) used to penalise ads/intros/outros."""

    segments: list[tuple[float, float, str, float]] = field(default_factory=list)  # start, end, label, interest

    def label_overlap(self, start: float, end: float, label: str) -> float:
        total = 0.0
        for s, e, lab, _ in self.segments:
            if lab == label:
                total += max(0.0, min(e, end) - max(s, start))
        return total / max(0.01, end - start)

    def interest(self, start: float, end: float) -> float | None:
        acc = w = 0.0
        for s, e, _, interest in self.segments:
            ov = max(0.0, min(e, end) - max(s, start))
            if ov > 0 and interest is not None:
                acc += ov * interest
                w += ov
        return acc / w if w else None


@dataclass
class Candidate:
    first_sentence: int
    last_sentence: int
    start: float
    end: float
    text: str
    first_word: int
    last_word: int
    metrics: dict[str, float] = field(default_factory=dict)
    penalties: dict[str, float] = field(default_factory=dict)
    heuristic: float = 0.0
    llm: dict | None = None
    llm_score: float | None = None
    vision: dict | None = None
    final: float = 0.0
    breakdown: ScoreBreakdown | None = None
    duplicate_of: int | None = None
    similarity: float | None = None
    pass_reached: int = 3
    notes: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end - self.start

    def iou(self, other: Candidate) -> float:
        inter = max(0.0, min(self.end, other.end) - max(self.start, other.start))
        union = max(self.end, other.end) - min(self.start, other.start)
        return inter / union if union > 0 else 0.0

    def overlap_ratio(self, other: Candidate) -> float:
        """Intersection relative to the shorter clip."""
        inter = max(0.0, min(self.end, other.end) - max(self.start, other.start))
        return inter / max(0.01, min(self.duration, other.duration))


@dataclass
class GenerationConfig:
    min_duration: float = 15.0
    max_duration: float = 60.0
    target_duration: float = 35.0
    max_internal_gap: float = 3.0
    top_k: int = 16
    nms_overlap: float = 0.45


def _span_text(sentences: list[Sentence], i: int, j: int) -> str:
    return " ".join(s.text for s in sentences[i : j + 1])


def score_span(i: int, j: int, sentences: list[Sentence], feats: list[SentenceFeatures], audio: AudioFeatures | None,
               activity: np.ndarray | None, cuts: list[float], doc: DocumentStats, cfg: GenerationConfig,
               timeline: TimelineContext | None = None,
               weights: dict[str, float] | None = None) -> tuple[dict[str, float], dict[str, float], ScoreBreakdown]:
    """Compute heuristic metrics (0-100) and penalties (points) for sentences i..j."""
    span = feats[i : j + 1]
    start, end = sentences[i].start, sentences[j].end
    dur = max(0.1, end - start)
    first, last = span[0], span[-1]
    n_words = sum(f.n_words for f in span)
    content = [t for f in span for t in f.content]

    # --- opening (first ~5 s)
    opening = [f for f in span if f.start < start + 5.0] or [first]
    hook_raw = (
        30 * first.question_opener + 12 * first.is_question
        + 14 * min(2, sum(f.hook_hits for f in opening))
        + 6 * min(3, sum(f.curiosity_hits for f in opening))
        + 7 * min(2, sum(f.emotion_hits for f in opening))
        + 9 * any(f.has_number for f in opening)
        + 5 * min(2, first.direct_address)
    )
    opening_energy = audio.energy_at(start, min(end, start + 3.0)) if audio else -30.0
    energy_bonus = 0.0
    if audio:
        energy_bonus = 12 * (sigmoid01(opening_energy - audio.loudness_ref_db, 0.0, 3.0) - 0.5)
    hook = clamp(38 + hook_raw + energy_bonus - 18 * first.weak_open - 14 * first.dangling_open)

    curiosity = clamp(35 + 8 * sum(f.curiosity_hits for f in opening) + 20 * any(f.is_question for f in opening)
                      + 6 * sum(f.hook_hits for f in span[:2]))

    # A span that starts right after a sentence that did not finish (pause-split) begins mid-thought.
    mid_thought = i > 0 and not feats[i - 1].terminal and first.start - feats[i - 1].end < 1.5
    standalone = clamp(88 - 26 * first.dangling_open - 16 * first.weak_open
                       - 30 * any(f.context_ref for f in span) - 30 * mid_thought)

    emotion_density = sum(f.emotion_hits for f in span) / dur * 10
    variation = audio.energy_variation(start, end) if audio else 4.0
    emotion = clamp(30 + 18 * emotion_density + 3.5 * variation + 12 * any(f.laughter for f in span))

    tail = span[-2:] if len(span) > 1 else span
    payoff = clamp(40 + 18 * last.terminal + 14 * min(2, sum(f.payoff_hits for f in tail))
                   + 8 * min(2, sum(f.emotion_hits for f in tail)) + 10 * any(f.laughter for f in tail)
                   + 12 * (any(f.is_question for f in span[:2]) and not last.is_question and len(span) > 2)
                   - 14 * last.is_question)
    if audio:
        closing = audio.energy_at(max(start, end - 3.0), end)
        payoff = clamp(payoff + 6 * (sigmoid01(closing - audio.loudness_ref_db, 0.0, 3.0) - 0.5))

    words_per_s = n_words / dur
    info = clamp(20 + 16 * (len(content) / dur) + 10 * min(3, sum(f.has_number for f in span)))

    if activity is not None and len(activity):
        a, b = int(start), max(int(end), int(start) + 1)
        act = float(activity[a:b].mean()) if b <= len(activity) else float(activity[a:].mean() if a < len(activity) else 0)
    else:
        act = 0.2
    n_cuts = sum(1 for c in cuts if start < c < end)
    cut_rate = n_cuts / dur * 10
    visual = clamp(35 + 60 * act + 10 * min(cut_rate, 3))

    mean_prob = sum(f.mean_prob * f.n_words for f in span) / max(1, n_words)
    filler_ratio = sum(f.filler_count for f in span) / max(1, n_words)
    rate_fit = 1.0 - min(1.0, abs(words_per_s - 2.9) / 1.8)
    speech_quality = clamp(100 * (0.55 * mean_prob + 0.25 * rate_fit + 0.20 * (1 - min(1, filler_ratio * 6))))

    dur_fit = 1.0 - min(1.0, abs(dur - cfg.target_duration) / max(10.0, cfg.target_duration))
    story = clamp(30 + 20 * (not first.weak_open and not first.dangling_open) + 22 * last.terminal
                  + 14 * dur_fit + 8 * min(3, len(span) - 1) / 3 + 6 * (sum(f.payoff_hits for f in tail) > 0))

    novelty = clamp(55 + 18 * doc.novelty_z(content))

    captionability = clamp(100 * (0.6 * mean_prob + 0.4 * rate_fit))

    speech_ratio = audio.speech_ratio(start, end) if audio else 0.9
    pacing = clamp(100 * (0.6 * speech_ratio + 0.4 * rate_fit))

    shareability = clamp(30 + 10 * min(3, sum(f.hook_hits for f in span)) + 8 * min(3, emotion_density)
                         + 10 * any(f.laughter for f in span) + 6 * min(2, sum(f.direct_address for f in span) / 3))
    rewatch = clamp(30 + 20 * any(f.laughter for f in span) + 10 * (payoff > 70) + 8 * (dur < 35)
                    + 6 * min(2, emotion_density))

    metrics = {
        "hook": hook, "curiosity": curiosity, "standalone": standalone, "emotion": emotion, "payoff": payoff,
        "information_density": info, "visual_activity": visual, "speech_quality": speech_quality,
        "story_completeness": story, "novelty": novelty, "captionability": captionability, "pacing": pacing,
        "shareability": shareability, "rewatch": rewatch,
    }
    if timeline and timeline.segments:
        interest = timeline.interest(start, end)
        if interest is not None:
            metrics["shareability"] = clamp(0.6 * metrics["shareability"] + 0.4 * interest)

    penalties: dict[str, float] = {}
    longest_gap = audio.longest_silence(start, end) if audio else 0.0
    if longest_gap > 1.0 or speech_ratio < 0.75:
        penalties["dead_air"] = round(min(15.0, 4 * max(0.0, longest_gap - 1.0) + 25 * max(0.0, 0.75 - speech_ratio)), 2)
    if mid_thought:
        penalties["missing_context"] = 14.0
    elif first.dangling_open or first.context_ref or any(f.context_ref for f in span):
        penalties["missing_context"] = 6.0 if first.dangling_open else 4.0
    if not last.terminal:
        next_close = j + 1 < len(feats) and feats[j + 1].start - last.end < 1.0
        penalties["abrupt_ending"] = 12.0 if next_close else 7.0
    if mean_prob < 0.6:
        penalties["poor_audio"] = round(min(10.0, (0.6 - mean_prob) * 40), 2)
    if act < 0.03 and n_cuts == 0:
        penalties["low_visual_interest"] = 2.0
    if hook < 45 and payoff > 65 and dur > 40:
        penalties["excessive_setup"] = 4.0
    if first.weak_open and first.n_words <= 3:
        penalties["weak_opening"] = 3.0
    sponsor = sum(f.sponsor_hits for f in span)
    sponsor_overlap = timeline.label_overlap(start, end, "SPONSOR") if timeline else 0.0
    if sponsor >= 2 or sponsor_overlap > 0.3:
        penalties["sponsor_segment"] = round(min(40.0, 8 * sponsor + 40 * sponsor_overlap), 2)
    if timeline:
        outro = timeline.label_overlap(start, end, "OUTRO") + timeline.label_overlap(start, end, "INTRO")
        if outro > 0.5:
            penalties["missing_context"] = penalties.get("missing_context", 0.0) + 4.0

    breakdown = combine(metrics, penalties, weights)
    return metrics, penalties, breakdown


def generate_candidates(sentences: list[Sentence], feats: list[SentenceFeatures], audio: AudioFeatures | None,
                        activity: np.ndarray | None, cuts: list[float], cfg: GenerationConfig,
                        timeline: TimelineContext | None = None,
                        weights: dict[str, float] | None = None) -> list[Candidate]:
    """Enumerate sentence spans within the duration window and score them."""
    doc = DocumentStats(feats)
    out: list[Candidate] = []
    n = len(sentences)
    for i in range(n):
        # A span must not *start* on an obviously dependent fragment when a better start exists nearby;
        # we still enumerate it (the scorer penalises it) so short videos keep options.
        for j in range(i, n):
            start, end = sentences[i].start, sentences[j].end
            dur = end - start
            if dur > cfg.max_duration:
                break
            if j > i and sentences[j].start - sentences[j - 1].end > cfg.max_internal_gap:
                break
            if dur < cfg.min_duration:
                continue
            metrics, penalties, breakdown = score_span(i, j, sentences, feats, audio, activity, cuts, doc, cfg,
                                                       timeline, weights)
            out.append(Candidate(
                first_sentence=i, last_sentence=j, start=start, end=end, text=_span_text(sentences, i, j),
                first_word=sentences[i].first_word, last_word=sentences[j].last_word,
                metrics=metrics, penalties=penalties, heuristic=breakdown.final, final=breakdown.final,
                breakdown=breakdown,
            ))
    return out


def select_diverse(candidates: list[Candidate], top_k: int, max_overlap: float = 0.45) -> list[Candidate]:
    """Greedy non-maximum suppression on time overlap, keeping the best-scoring diverse spans."""
    ranked = sorted(candidates, key=lambda c: c.final, reverse=True)
    chosen: list[Candidate] = []
    for c in ranked:
        if all(c.overlap_ratio(o) <= max_overlap for o in chosen):
            chosen.append(c)
            if len(chosen) >= top_k:
                break
    return chosen
