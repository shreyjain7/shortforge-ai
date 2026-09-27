"""Local optimisation of ranking weights from real Short performance.

Guard rails against over-fitting:
* nothing changes until ``min_samples`` Shorts with mature metrics exist;
* performance is normalised (log views, z-scored, blended with like rate) so one viral outlier
  cannot dominate;
* each update moves weights by at most ``learning_rate`` toward the evidence and never more than
  ``max_change`` (relative) per update, and the total weight mass is preserved;
* findings are reported as correlations with sample sizes, never as causal claims.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import mean, pstdev
from typing import Any

from shortforge.engines.ranking.weights import DEFAULT_WEIGHTS, METRIC_LABELS, normalize_weights


@dataclass
class Sample:
    short_id: int
    metrics: dict[str, float]
    features: dict[str, Any]
    views: int
    likes: int | None
    age_hours: float


@dataclass
class LearningResult:
    updated: bool
    weights: dict[str, float]
    previous: dict[str, float]
    correlations: dict[str, float]
    insights: list[dict[str, Any]] = field(default_factory=list)
    samples: int = 0
    reason: str = ""


def pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = mean(xs), mean(ys)
    sx, sy = pstdev(xs), pstdev(ys)
    if sx == 0 or sy == 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=False)) / (n * sx * sy)


def performance_scores(samples: list[Sample]) -> dict[int, float]:
    """z-scored log views (age-adjusted) blended with z-scored like rate."""
    if not samples:
        return {}
    # Views grow with age; divide by log of age so a 3-day-old Short isn't compared raw with a 3-week-old one.
    lv = [math.log1p(s.views) / math.log(max(math.e, s.age_hours / 24 + math.e)) for s in samples]
    m, sd = mean(lv), pstdev(lv) or 1.0
    z_views = [(v - m) / sd for v in lv]
    rates = [(s.likes or 0) / max(1, s.views) for s in samples]
    mr, sr = mean(rates), pstdev(rates) or 1.0
    z_rate = [(r - mr) / sr for r in rates]
    return {s.short_id: 0.8 * zv + 0.2 * zr for s, zv, zr in zip(samples, z_views, z_rate, strict=False)}


def update_weights(samples: list[Sample], current: dict[str, float], *, min_samples: int = 20,
                   learning_rate: float = 0.15, max_change: float = 0.2) -> LearningResult:
    current = {**DEFAULT_WEIGHTS, **(current or {})}
    mature = [s for s in samples if s.age_hours >= 48]
    if len(mature) < min_samples:
        return LearningResult(False, current, current, {}, [], len(mature),
                              f"Need {min_samples} Shorts older than 48h with metrics (have {len(mature)}).")
    perf = performance_scores(mature)
    ys = [perf[s.short_id] for s in mature]
    correlations: dict[str, float] = {}
    new = dict(current)
    for metric, w in current.items():
        pairs = [(s.metrics[metric], y) for s, y in zip(mature, ys, strict=False) if metric in s.metrics]
        if len(pairs) < min_samples:
            continue
        r = pearson([p[0] for p in pairs], [p[1] for p in pairs])
        # Shrink correlations toward zero for small samples (roughly a standard-error penalty).
        r_shrunk = r * max(0.0, 1 - 1.5 / math.sqrt(len(pairs)))
        correlations[metric] = round(r, 3)
        target = w * (1 + r_shrunk)
        step = w + learning_rate * (target - w)
        lo, hi = w * (1 - max_change), w * (1 + max_change)
        new[metric] = min(hi, max(lo, step))
    new = normalize_weights(new)
    return LearningResult(True, new, current, correlations, insights(mature, perf), len(mature), "updated")


def _bucket_clip_length(d: float) -> str:
    for lo, hi in ((0, 20), (20, 30), (30, 40), (40, 50), (50, 60)):
        if lo <= d < hi:
            return f"{lo}-{hi}s"
    return "60s+"


def insights(samples: list[Sample], perf: dict[int, float], min_group: int = 3) -> list[dict[str, Any]]:
    """Group-level differences in normalised performance (correlational, with sample sizes)."""
    dims = {
        "clip_length": lambda s: _bucket_clip_length(float(s.features.get("duration", 0))),
        "hook_type": lambda s: s.features.get("hook_type") or "unknown",
        "upload_hour": lambda s: f"{int(s.features.get('upload_hour', -1)):02d}:00" if s.features.get("upload_hour") is not None else None,
        "caption_preset": lambda s: s.features.get("caption_preset"),
        "source": lambda s: s.features.get("source"),
        "speaker_count": lambda s: str(s.features.get("speaker_count")) if s.features.get("speaker_count") is not None else None,
        "zoom_frequency": lambda s: "high" if float(s.features.get("zooms_per_min", 0)) > 4 else "low",
        "opening_pace": lambda s: "fast" if float(s.features.get("opening_wps", 0)) > 3.2 else "normal",
    }
    overall = mean(perf.values()) if perf else 0.0
    out: list[dict[str, Any]] = []
    for dim, fn in dims.items():
        groups: dict[str, list[float]] = {}
        for s in samples:
            key = fn(s)
            if key is None:
                continue
            groups.setdefault(str(key), []).append(perf[s.short_id])
        for key, vals in groups.items():
            if len(vals) < min_group:
                continue
            diff = mean(vals) - overall
            if abs(diff) < 0.25:
                continue
            out.append({"dimension": dim, "group": key, "n": len(vals), "effect": round(diff, 2),
                        "text": f"{dim.replace('_', ' ').title()} = {key} correlated with "
                                f"{'stronger' if diff > 0 else 'weaker'} performance in this dataset "
                                f"({len(vals)} Shorts, {diff:+.2f} SD)."})
    out.sort(key=lambda x: -abs(x["effect"]))
    return out[:12]


def describe_weights(weights: dict[str, float]) -> list[dict[str, Any]]:
    return [{"metric": k, "label": METRIC_LABELS.get(k, k), "weight": round(v, 3),
             "default": DEFAULT_WEIGHTS.get(k)} for k, v in weights.items()]
