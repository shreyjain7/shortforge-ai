"""Word clean-up and sentence segmentation for word-level transcripts."""

from __future__ import annotations

import re

from shortforge.engines.transcription.types import Sentence, Word, words_text

_ABBREVIATIONS = {
    "mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "st.", "vs.", "etc.", "e.g.", "i.e.", "u.s.", "u.k.",
    "a.m.", "p.m.", "no.", "inc.", "ltd.", "co.", "approx.", "vol.", "fig.",
}
_TERMINAL = re.compile(r"[.!?…]+[\"')\]]*$")


def is_terminal(word: str) -> bool:
    w = word.strip().lower()
    if not w or not _TERMINAL.search(w):
        return False
    if w in _ABBREVIATIONS:
        return False
    # single capital letter initials like "J."
    return not re.fullmatch(r"[a-z]\.", w)


# Scripts written without spaces between words: never merge tokens for these languages.
NO_SPACE_LANGUAGES = {"zh", "ja", "th", "lo", "my", "km", "yue", "bo"}


def sanitize_words(words: list[Word], duration: float | None = None, language: str | None = None) -> list[Word]:
    """Make timestamps monotonic and non-degenerate; drop empty tokens; merge continuation
    tokens (e.g. Whisper's "2" + ",999") into the previous word; re-index."""
    out: list[Word] = []
    prev_end = 0.0
    merge_ok = language not in NO_SPACE_LANGUAGES
    for w in words:
        if not w.text.strip():
            continue
        if merge_ok and out and not w.text.startswith(" ") and w.start - out[-1].end < 0.25:
            last = out[-1]
            last.text += w.text
            last.end = round(max(last.end, w.end), 3)
            last.prob = min(last.prob, w.prob)
            prev_end = last.end
            continue
        start = max(w.start, prev_end - 0.02)
        end = max(w.end, start + 0.04)
        if duration:
            end = min(end, duration)
            start = min(start, end - 0.01)
        out.append(Word(len(out), w.text, round(start, 3), round(end, 3), w.prob, w.segment_idx))
        prev_end = end
    return out


def build_sentences(words: list[Word], *, pause_split: float = 1.2, max_words: int = 45,
                    segment_pause_split: float = 0.6) -> list[Sentence]:
    """Group words into sentences.

    Boundaries: terminal punctuation, a long pause, a Whisper segment boundary followed by a
    moderate pause, or an over-long run (split at the last comma when possible).
    Assigns ``sentence_idx`` on each word in-place.
    """
    sentences: list[Sentence] = []
    if not words:
        return sentences
    start_i = 0

    def close(end_i: int) -> None:
        nonlocal start_i
        chunk = words[start_i : end_i + 1]
        sid = len(sentences)
        for w in chunk:
            w.sentence_idx = sid
        sentences.append(Sentence(sid, chunk[0].start, chunk[-1].end, chunk[0].idx, chunk[-1].idx,
                                  words_text(chunk)))
        start_i = end_i + 1

    for i, w in enumerate(words):
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap = (nxt.start - w.end) if nxt else 0.0
        count = i - start_i + 1
        if nxt is None:
            close(i)
            break
        if is_terminal(w.text) or gap >= pause_split or (nxt.segment_idx != w.segment_idx and gap >= segment_pause_split):
            close(i)
        elif count >= max_words:
            # prefer to split after the most recent comma in this run
            comma = next((j for j in range(i, start_i + 4, -1) if words[j].text.strip().endswith((",", ";", ":"))),
                         None)
            close(comma if comma is not None else i)
    # A trailing close may have left a dangling run if the comma split happened on the last word.
    if start_i < len(words):
        close(len(words) - 1)
    return sentences
