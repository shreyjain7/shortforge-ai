"""Semantic keyword emphasis: rank words by meaning, never emphasise filler.

"this graphics card is almost twice as fast" -> GRAPHICS CARD, TWICE AS FAST
"""

from __future__ import annotations

import re

from shortforge.engines.clip_detection import lexicon as lx

_NUM = re.compile(r"\d|%|\$|€|£")
_INTENSIFIERS = {"twice", "double", "triple", "half", "fastest", "slowest", "biggest", "smallest", "cheapest",
                 "never", "always", "only", "first", "last", "every", "zero", "nothing", "everything", "free",
                 "insane", "impossible", "secret", "million", "billion", "thousand", "percent"}
_PHRASE_GLUE = {"as", "of", "the", "a", "and"}


def _norm(token: str) -> str:
    return re.sub(r"[^\w%$€£'-]", "", token.lower())


def word_importance(words: list[str], keywords: list[str] | None = None,
                    idf: dict[str, float] | None = None) -> list[float]:
    norms = [_norm(w) for w in words]
    scores = [0.0] * len(words)
    for i, (raw, n) in enumerate(zip(words, norms, strict=False)):
        if not n or n in lx.STOPWORDS or n in lx.FILLERS or (len(n) <= 2 and not _NUM.search(n)):
            continue
        s = 1.0
        if _NUM.search(raw):
            s += 2.5
        if n in _INTENSIFIERS or n in lx.EMOTION_WORDS:
            s += 1.6
        if n in lx.CURIOSITY_WORDS:
            s += 0.6
        clean = raw.strip(" \"'.,!?;:")
        if i > 0 and clean[:1].isupper() and not words[i - 1].strip().endswith((".", "!", "?")):
            s += 1.2  # proper noun mid-sentence
        if idf:
            s += 0.35 * min(4.0, idf.get(n, 1.0))
        scores[i] = s
    # Phrase-level keywords (grounded LLM keywords) boost every word they cover.
    for kw in keywords or []:
        parts = [_norm(p) for p in kw.split() if _norm(p)]
        if not parts:
            continue
        for i in range(len(norms) - len(parts) + 1):
            if norms[i : i + len(parts)] == parts:
                for k in range(i, i + len(parts)):
                    # Connector words inside a phrase ("twice AS fast") are added by the glue pass
                    # below, so they don't consume the emphasis budget.
                    if norms[k] not in lx.STOPWORDS or norms[k] in _INTENSIFIERS:
                        scores[k] = max(scores[k], 1.0) + 2.2
    return scores


def select_emphasis(words: list[str], keywords: list[str] | None = None, idf: dict[str, float] | None = None,
                    ratio: float = 0.14, min_score: float = 2.4) -> list[bool]:
    """Pick ~ratio of words, keeping multi-word phrases together ("TWICE AS FAST")."""
    scores = word_importance(words, keywords, idf)
    n = len(words)
    budget = max(1, round(n * ratio)) if n else 0
    order = sorted(range(n), key=lambda i: scores[i], reverse=True)
    chosen = [False] * n
    used = 0
    for i in order:
        if used >= budget or scores[i] < min_score:
            break
        if not chosen[i]:
            chosen[i] = True
            used += 1
    # Glue short connector words between two emphasised words ("twice AS fast").
    for i in range(1, n - 1):
        if not chosen[i] and chosen[i - 1] and chosen[i + 1] and _norm(words[i]) in _PHRASE_GLUE:
            chosen[i] = True
    return chosen
