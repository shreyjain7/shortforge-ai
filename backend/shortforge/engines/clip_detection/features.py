"""Per-sentence and per-candidate features for heuristic clip scoring."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from shortforge.engines.clip_detection import lexicon as lx
from shortforge.engines.transcription.types import Sentence, Word

_TOKEN = re.compile(r"[a-z0-9$%€£']+(?:[.,][0-9]+)?")
_NUMBER = re.compile(r"(\d|\bmillion\b|\bbillion\b|\bthousand\b|\bhundred\b|\bpercent\b|%|\$)")


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def content_tokens(text: str) -> list[str]:
    return [t for t in tokens(text) if t not in lx.STOPWORDS and len(t) > 2]


def phrase_hits(text: str, phrases: tuple[str, ...]) -> int:
    low = f" {text.lower()} "
    return sum(1 for p in phrases if f" {p}" in low or (p.startswith(("[", "(")) and p in low))


@dataclass
class SentenceFeatures:
    idx: int
    start: float
    end: float
    n_words: int
    is_question: bool
    question_opener: bool
    hook_hits: int
    curiosity_hits: int
    emotion_hits: int
    payoff_hits: int
    has_number: bool
    direct_address: int
    laughter: bool
    filler_count: int
    weak_open: bool
    dangling_open: bool
    context_ref: bool
    terminal: bool
    sponsor_hits: int
    mean_prob: float
    energy_db: float = -40.0
    content: list[str] = field(default_factory=list)


def sentence_features(sentences: list[Sentence], words: list[Word]) -> list[SentenceFeatures]:
    feats: list[SentenceFeatures] = []
    for s in sentences:
        sw = words[s.first_word : s.last_word + 1]
        text = s.text
        toks = tokens(text)
        first = toks[0] if toks else ""
        low = text.lower()
        filler = sum(1 for t in toks if t in lx.FILLERS) + sum(low.count(p) for p in lx.FILLER_PHRASES)
        feats.append(SentenceFeatures(
            idx=s.idx,
            start=s.start,
            end=s.end,
            n_words=len(sw),
            is_question=text.rstrip().endswith("?"),
            question_opener=first in lx.QUESTION_OPENERS and text.rstrip().endswith("?"),
            hook_hits=phrase_hits(text, lx.HOOK_PHRASES),
            curiosity_hits=sum(1 for t in toks if t in lx.CURIOSITY_WORDS),
            emotion_hits=sum(1 for t in toks if t in lx.EMOTION_WORDS) + text.count("!"),
            payoff_hits=phrase_hits(text, lx.PAYOFF_PHRASES),
            has_number=bool(_NUMBER.search(low)),
            direct_address=sum(1 for t in toks if t in ("you", "your", "you're", "yourself")),
            laughter=any(m in low for m in lx.LAUGHTER),
            filler_count=filler,
            weak_open=first in lx.WEAK_OPENERS,
            dangling_open=first in lx.DANGLING_START,
            context_ref=phrase_hits(text, lx.CONTEXT_REFS) > 0,
            terminal=bool(re.search(r"[.!?…][\"')\]]*$", text.strip())),
            sponsor_hits=phrase_hits(text, lx.SPONSOR_PHRASES),
            mean_prob=sum(w.prob for w in sw) / max(1, len(sw)),
            content=content_tokens(text),
        ))
    return feats


class DocumentStats:
    """IDF-like weights of content words across the whole transcript (for novelty/emphasis)."""

    def __init__(self, sentences: list[SentenceFeatures]) -> None:
        self.df: Counter[str] = Counter()
        for f in sentences:
            self.df.update(set(f.content))
        self.n = max(1, len(sentences))
        # Calibrate novelty against this document's own sentence distribution.
        per_sentence = [self.distinctiveness(f.content) for f in sentences if f.content]
        self._mean = sum(per_sentence) / len(per_sentence) if per_sentence else 0.0
        var = sum((x - self._mean) ** 2 for x in per_sentence) / len(per_sentence) if per_sentence else 1.0
        self._std = max(1e-6, var ** 0.5)

    def idf(self, token: str) -> float:
        return math.log((1 + self.n) / (1 + self.df.get(token, 0))) + 1.0

    def novelty_z(self, toks: list[str]) -> float:
        """How distinctive a span's vocabulary is relative to an average sentence (z-score, clipped)."""
        return max(-2.5, min(2.5, (self.distinctiveness(toks) - self._mean) / self._std))

    def distinctiveness(self, toks: list[str]) -> float:
        if not toks:
            return 0.0
        counts = Counter(toks)
        # Words that recur *within* the clip but are rare in the document define its topic.
        score = sum(min(c, 3) * self.idf(t) for t, c in counts.items())
        return score / len(toks)


def clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def sigmoid01(x: float, center: float, scale: float) -> float:
    return 1.0 / (1.0 + math.exp(-(x - center) / scale))
