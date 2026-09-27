"""PASS 7 - duplicate detection across candidates and previously generated Shorts.

Signals (any one can flag a duplicate):
* source-range overlap on the same video,
* transcript overlap (word 3-shingle Jaccard / containment),
* semantic similarity of embeddings when an embedding model is available,
* visual/audio fingerprints for rendered outputs (see :func:`fingerprint_similarity`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from shortforge.engines.clip_detection.features import tokens


def shingles(text: str, n: int = 3) -> set[tuple[str, ...]]:
    toks = tokens(text)
    if len(toks) < n:
        return {tuple(toks)} if toks else set()
    return {tuple(toks[i : i + n]) for i in range(len(toks) - n + 1)}


def text_similarity(a: str, b: str) -> float:
    """Max of Jaccard and containment of word 3-shingles (containment catches sub-clips)."""
    sa, sb = shingles(a), shingles(b)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    jaccard = inter / len(sa | sb)
    containment = inter / min(len(sa), len(sb))
    return max(jaccard, 0.9 * containment)


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def time_overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    return inter / max(0.01, min(a[1] - a[0], b[1] - b[0]))


@dataclass
class ExistingClip:
    key: str  # e.g. "short:183" or "candidate:42"
    video_id: int | None
    start: float
    end: float
    text: str
    embedding: list[float] | None = None


@dataclass
class DuplicateMatch:
    key: str
    similarity: float
    reason: str


def find_duplicate(video_id: int | None, start: float, end: float, text: str, existing: list[ExistingClip],
                   threshold: float = 0.8, embedding: list[float] | None = None) -> DuplicateMatch | None:
    best: DuplicateMatch | None = None
    for ex in existing:
        sim, reason = 0.0, ""
        if video_id is not None and ex.video_id == video_id:
            ov = time_overlap((start, end), (ex.start, ex.end))
            if ov > sim:
                sim, reason = ov, "overlapping source range"
        ts = text_similarity(text, ex.text)
        if ts > sim:
            sim, reason = ts, "transcript overlap"
        if embedding is not None and ex.embedding is not None:
            es = cosine(embedding, ex.embedding)
            # Embeddings of unrelated speech are rarely below ~0.5; rescale so 0.97+ ~ duplicate.
            es_scaled = max(0.0, (es - 0.75) / 0.25)
            if es_scaled > sim:
                sim, reason = es_scaled, "semantic similarity"
        if sim >= threshold and (best is None or sim > best.similarity):
            best = DuplicateMatch(ex.key, round(sim, 3), reason)
    return best


def fingerprint_similarity(a: list[int], b: list[int]) -> float:
    """Similarity of two per-second perceptual hash sequences (64-bit dHash ints)."""
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    best = 0.0
    # Allow small temporal offsets.
    for shift in range(-3, 4):
        matches = 0
        count = 0
        for i in range(n):
            j = i + shift
            if 0 <= j < len(b):
                count += 1
                if bin(a[i] ^ b[j]).count("1") <= 10:
                    matches += 1
        if count:
            best = max(best, matches / count)
    return best
