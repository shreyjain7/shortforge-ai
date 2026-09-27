"""PASS 6 - boundary refinement.

Rules:
* never start or end inside a word: boundaries sit in the gap between words;
* keep a short lead-in before the first word and a natural tail after the last word;
* snap to a nearby hard scene cut when that does not clip speech (visually clean edits);
* apply LLM trims/extensions only when the result stays within the allowed duration.
"""

from __future__ import annotations

from dataclasses import dataclass

from shortforge.engines.clip_detection.candidates import Candidate
from shortforge.engines.transcription.types import Sentence, Word


@dataclass
class BoundaryConfig:
    lead_in: float = 0.12
    tail: float = 0.35
    min_word_gap: float = 0.03
    cut_snap_window: float = 0.45
    min_duration: float = 10.0
    max_duration: float = 180.0


def apply_llm_adjustments(c: Candidate, sentences: list[Sentence], adj: dict, cfg: BoundaryConfig) -> tuple[int, int]:
    first, last = c.first_sentence, c.last_sentence
    trim_s, trim_e, ext = int(adj.get("trim_start", 0)), int(adj.get("trim_end", 0)), int(adj.get("extend_end", 0))
    new_first = min(first + trim_s, last)
    new_last = max(new_first, last - trim_e)
    if ext:
        new_last = min(len(sentences) - 1, new_last + ext)
    dur = sentences[new_last].end - sentences[new_first].start
    if cfg.min_duration <= dur <= cfg.max_duration:
        return new_first, new_last
    # Try extension only / trims only as fallbacks.
    if ext:
        alt_last = min(len(sentences) - 1, last + ext)
        if cfg.min_duration <= sentences[alt_last].end - sentences[first].start <= cfg.max_duration:
            return first, alt_last
    return first, last


def refine(first_s: int, last_s: int, sentences: list[Sentence], words: list[Word], cuts: list[float],
           video_duration: float, cfg: BoundaryConfig) -> tuple[float, float, int, int]:
    """Return (start, end, first_word_idx, last_word_idx) for the sentence span."""
    fw = sentences[first_s].first_word
    lw = sentences[last_s].last_word
    first_word, last_word = words[fw], words[lw]
    prev_end = words[fw - 1].end if fw > 0 else 0.0
    next_start = words[lw + 1].start if lw + 1 < len(words) else video_duration

    start = max(prev_end + cfg.min_word_gap, first_word.start - cfg.lead_in, 0.0)
    end = min(next_start - cfg.min_word_gap, last_word.end + cfg.tail, video_duration)
    if end <= last_word.end:  # next word is glued to this one; end exactly at the word end
        end = min(video_duration, last_word.end + 0.02)

    # Snap start to a scene cut shortly before the first word (clean visual start, no speech lost).
    for c in cuts:
        if first_word.start - cfg.cut_snap_window <= c <= first_word.start and c >= prev_end:
            start = c
            break
    # Snap end to a scene cut shortly after the last word (avoid a 2-frame flash of the next shot).
    for c in cuts:
        if last_word.end <= c <= last_word.end + cfg.cut_snap_window and c <= next_start:
            end = max(last_word.end + 0.02, c - 0.04)
            break
    # Also avoid showing a sliver of the next shot when a cut falls just inside our tail.
    for c in cuts:
        if last_word.end < c < end:
            end = max(last_word.end + 0.02, c - 0.04)
            break
    return round(start, 3), round(end, 3), fw, lw
