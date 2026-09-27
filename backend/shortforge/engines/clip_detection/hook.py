"""Hook optimiser: scores the first 1 s / 3 s / 5 s of a candidate and suggests a better start."""

from __future__ import annotations

from dataclasses import dataclass

from shortforge.engines.audio.analysis import AudioFeatures
from shortforge.engines.clip_detection import lexicon as lx
from shortforge.engines.clip_detection.features import SentenceFeatures, clamp, phrase_hits, tokens
from shortforge.engines.transcription.types import Sentence, Word


@dataclass
class HookReport:
    window_1s: float
    window_3s: float
    window_5s: float
    opening_words: str
    suggested_shift: int
    """Number of leading sentences to drop for a stronger opening (0 = keep)."""
    reason: str

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _window_score(words: list[Word], start: float, secs: float, audio: AudioFeatures | None) -> tuple[float, str]:
    ws = [w for w in words if start <= w.start < start + secs]
    text = " ".join(w.clean for w in ws)
    toks = tokens(text)
    if not toks:
        return 10.0, ""
    s = 35.0
    s += 22 * (toks[0] in lx.QUESTION_OPENERS) + 12 * ("?" in text)
    s += 14 * min(2, phrase_hits(text, lx.HOOK_PHRASES))
    s += 7 * min(3, sum(t in lx.EMOTION_WORDS or t in lx.CURIOSITY_WORDS for t in toks))
    s += 8 * any(c.isdigit() for c in text)
    s += 5 * min(2, sum(t in ("you", "your") for t in toks))
    s -= 16 * (toks[0] in lx.WEAK_OPENERS) + 12 * (toks[0] in lx.DANGLING_START)
    s -= 6 * sum(t in lx.FILLERS for t in toks[:4])
    first_delay = ws[0].start - start
    s -= 20 * max(0.0, first_delay - 0.3)  # dead air before speech
    if audio:
        s += 8 * (audio.energy_at(start, start + secs) > audio.loudness_ref_db)
    return clamp(s), text


def analyze_hook(first_s: int, last_s: int, start: float, sentences: list[Sentence], feats: list[SentenceFeatures],
                 words: list[Word], audio: AudioFeatures | None, min_duration: float) -> HookReport:
    w1, text1 = _window_score(words, start, 1.0, audio)
    w3, _ = _window_score(words, start, 3.0, audio)
    w5, _ = _window_score(words, start, 5.0, audio)
    shift, reason = 0, ""
    # Consider dropping up to 2 weak leading sentences if a clearly stronger opening follows.
    base = 0.5 * w3 + 0.5 * w5
    for k in (1, 2):
        ns = first_s + k
        if ns > last_s or sentences[last_s].end - sentences[ns].start < min_duration:
            break
        f = feats[ns]
        if f.dangling_open or f.context_ref:
            continue
        a3, _ = _window_score(words, sentences[ns].start, 3.0, audio)
        a5, _ = _window_score(words, sentences[ns].start, 5.0, audio)
        alt = 0.5 * a3 + 0.5 * a5
        if alt > base + 12:
            shift, base = k, alt
            reason = f"Sentence {k + 1} opens stronger ({alt:.0f} vs {0.5 * w3 + 0.5 * w5:.0f})"
    return HookReport(round(w1, 1), round(w3, 1), round(w5, 1), text1, shift, reason)
