"""Transcript data structures shared by all analysis engines."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Word:
    idx: int
    text: str
    """Raw token as produced by Whisper (usually has a leading space)."""
    start: float
    end: float
    prob: float = 1.0
    segment_idx: int = 0
    sentence_idx: int = 0

    @property
    def clean(self) -> str:
        return self.text.strip()

    @property
    def norm(self) -> str:
        return re.sub(r"[^\w'%$€£#@+-]", "", self.clean.lower())


@dataclass
class Sentence:
    idx: int
    start: float
    end: float
    first_word: int
    last_word: int
    text: str

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Segment:
    idx: int
    start: float
    end: float
    text: str
    avg_logprob: float = 0.0
    no_speech_prob: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TranscriptResult:
    language: str | None
    language_probability: float | None
    duration: float
    words: list[Word]
    segments: list[Segment]
    sentences: list[Sentence] = field(default_factory=list)
    model: str = ""
    device: str = ""
    compute_type: str = ""
    elapsed_s: float = 0.0

    @property
    def text(self) -> str:
        return words_text(self.words)


def _no_space_script(text: str) -> bool:
    """CJK / Thai text is written without spaces between words."""
    return any("぀" <= ch <= "ヿ" or "一" <= ch <= "鿿" or "฀" <= ch <= "๿"
               for ch in text)


def words_text(words: list[Word]) -> str:
    """Join Whisper tokens back into readable text.

    Whisper tokens carry their own leading space. A space is added only when neither side provides
    one and the next token starts a new alphanumeric word in a space-separated script.
    """
    if not words:
        return ""
    parts = [words[0].text]
    for w in words[1:]:
        needs_space = (not w.text[:1].isspace() and not parts[-1][-1:].isspace() and w.text[:1].isalnum()
                       and not _no_space_script(w.text))
        parts.append(" " + w.text if needs_space else w.text)
    return re.sub(r"\s+", " ", "".join(parts)).strip()
