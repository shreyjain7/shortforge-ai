"""Semantic timeline: labels such as INTRO / STORY / HUMOR / SPONSOR over the whole video.

Uses the local LLM when available, chunk by chunk. Without an LLM a transparent keyword/position
heuristic is used and each segment is marked ``source="heuristic"`` (never presented as AI output).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from shortforge.core.logging import get_logger
from shortforge.engines.clip_detection import lexicon as lx
from shortforge.engines.clip_detection.features import SentenceFeatures, phrase_hits
from shortforge.engines.llm.base import LLMProvider
from shortforge.engines.transcription.types import Sentence

log = get_logger("semantic")

LABELS = ["INTRO", "STORY", "EXPLANATION", "DISCUSSION", "HUMOR", "HIGH_IMPACT", "PAYOFF", "TUTORIAL", "OPINION",
          "QA", "SPONSOR", "OUTRO", "TRANSITION"]

SYSTEM = (
    "You label sections of a video transcript for a short-form video editor. Be precise and critical. "
    "Only use the provided labels. SPONSOR means an ad read or self-promotion (sponsors, merch, subscribe requests). "
    "HIGH_IMPACT means a surprising claim, strong reaction or peak moment. PAYOFF means a resolution, answer, "
    "punchline or conclusion. 'interest' is 0-100: how compelling the section would be as a standalone vertical "
    "short for a stranger (most sections 30-65; 80+ only for genuinely exceptional moments)."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "from": {"type": "integer"},
                    "to": {"type": "integer"},
                    "label": {"type": "string", "enum": LABELS},
                    "summary": {"type": "string"},
                    "interest": {"type": "integer"},
                },
                "required": ["from", "to", "label", "summary", "interest"],
            },
        }
    },
    "required": ["segments"],
}


@dataclass
class SemanticSegment:
    start: float
    end: float
    label: str
    summary: str
    interest: float
    source: str


def chunk_sentences(sentences: list[Sentence], target_s: float = 100.0, max_sentences: int = 28) -> list[tuple[int, int]]:
    chunks: list[tuple[int, int]] = []
    i = 0
    while i < len(sentences):
        j = i
        while j + 1 < len(sentences) and sentences[j + 1].end - sentences[i].start <= target_s and j + 1 - i < max_sentences:
            j += 1
        chunks.append((i, j))
        i = j + 1
    return chunks


def heuristic_timeline(sentences: list[Sentence], feats: list[SentenceFeatures], duration: float) -> list[SemanticSegment]:
    out: list[SemanticSegment] = []
    for a, b in chunk_sentences(sentences, target_s=45.0, max_sentences=14):
        text = " ".join(s.text for s in sentences[a : b + 1])
        start, end = sentences[a].start, sentences[b].end
        span = feats[a : b + 1]
        if sum(f.sponsor_hits for f in span) >= 2:
            label = "SPONSOR"
        elif start < max(30.0, 0.06 * duration) and phrase_hits(text, lx.INTRO_PHRASES):
            label = "INTRO"
        elif end > duration - max(40.0, 0.06 * duration) and phrase_hits(text, lx.OUTRO_PHRASES):
            label = "OUTRO"
        elif any(f.laughter for f in span):
            label = "HUMOR"
        elif sum(f.is_question for f in span) >= 2:
            label = "QA"
        elif sum(f.emotion_hits for f in span) >= 4:
            label = "HIGH_IMPACT"
        else:
            label = "DISCUSSION"
        interest = min(90.0, 35 + 4 * sum(f.hook_hits + f.emotion_hits for f in span) / max(1, len(span)) * 3)
        out.append(SemanticSegment(start, end, label, "", round(interest, 1), "heuristic"))
    return out


def llm_timeline(llm: LLMProvider, sentences: list[Sentence], video_title: str,
                 progress: Callable[[float, str], None] | None = None,
                 feats: list[SentenceFeatures] | None = None, system: str | None = None) -> list[SemanticSegment]:
    chunks = chunk_sentences(sentences)
    out: list[SemanticSegment] = []
    for ci, (a, b) in enumerate(chunks):
        lines = "\n".join(f"[{k}] {sentences[k].text}" for k in range(a, b + 1))
        user = (
            f'Video title: "{video_title}"\n'
            f"Transcript sentences {a}-{b}:\n{lines}\n\n"
            f"Split sentences {a}..{b} into 1-5 contiguous sections covering every sentence exactly once. "
            "Return JSON with 'segments' (from/to are sentence numbers, inclusive)."
        )
        try:
            data = llm.chat_json(system or SYSTEM, user, schema=SCHEMA, max_tokens=700)
            segs = _validate(data.get("segments", []), a, b)
        except Exception as exc:  # one bad chunk must not kill the whole analysis
            log.warning("semantic labelling failed for chunk %d: %s", ci, exc)
            segs = [(a, b, "DISCUSSION", "", 45.0)]
        for f, t, label, summary, interest in segs:
            label = _cross_check(label, f, t, feats)
            out.append(SemanticSegment(sentences[f].start, sentences[t].end, label, summary[:300], interest, "llm"))
        if progress:
            progress((ci + 1) / len(chunks), f"Understanding content ({ci + 1}/{len(chunks)})")
    return _merge_adjacent(out)


def _cross_check(label: str, f: int, t: int, feats: list[SentenceFeatures] | None) -> str:
    """Small LLMs over-use SPONSOR; require actual ad-read language before accepting it
    (a false SPONSOR label would wrongly bury good clips)."""
    if label != "SPONSOR" or feats is None:
        return label
    hits = sum(x.sponsor_hits for x in feats[f : t + 1])
    return "SPONSOR" if hits >= 2 else "DISCUSSION"


def _validate(raw: list[dict], a: int, b: int) -> list[tuple[int, int, str, str, float]]:
    segs = []
    for s in raw:
        try:
            f, t = int(s["from"]), int(s["to"])
        except (KeyError, TypeError, ValueError):
            continue
        f, t = max(a, min(b, f)), max(a, min(b, t))
        if t < f:
            f, t = t, f
        label = s.get("label") if s.get("label") in LABELS else "DISCUSSION"
        interest = float(max(0, min(100, int(s.get("interest", 45)))))
        segs.append((f, t, label, str(s.get("summary", "")), interest))
    segs.sort()
    # Make them contiguous and non-overlapping over [a, b].
    fixed: list[tuple[int, int, str, str, float]] = []
    cursor = a
    for f, t, label, summary, interest in segs:
        if t < cursor:
            continue
        f = cursor
        fixed.append((f, t, label, summary, interest))
        cursor = t + 1
    if not fixed:
        return [(a, b, "DISCUSSION", "", 45.0)]
    if cursor <= b:
        f, _, label, summary, interest = fixed[-1]
        fixed[-1] = (f, b, label, summary, interest)
    return fixed


def _merge_adjacent(segs: list[SemanticSegment]) -> list[SemanticSegment]:
    merged: list[SemanticSegment] = []
    for s in segs:
        if merged and merged[-1].label == s.label and s.start - merged[-1].end < 2.0 and s.label in ("SPONSOR", "INTRO", "OUTRO"):
            m = merged[-1]
            merged[-1] = SemanticSegment(m.start, s.end, m.label, m.summary, max(m.interest, s.interest), m.source)
        else:
            merged.append(s)
    return merged
