"""Ranking weights and the final multi-factor score.

Scores are a transparent ranking heuristic, not a virality prediction. Weights start at hand-tuned
defaults and may be nudged by the learning engine once enough performance data exists.
"""

from __future__ import annotations

from dataclasses import dataclass

METRICS = (
    "hook", "curiosity", "standalone", "emotion", "payoff", "information_density", "visual_activity",
    "speech_quality", "story_completeness", "novelty", "captionability", "pacing", "shareability", "rewatch",
)
PENALTIES = (
    "dead_air", "missing_context", "abrupt_ending", "poor_audio", "low_visual_interest", "excessive_setup",
    "repeated_topic", "weak_opening", "sponsor_segment",
)

DEFAULT_WEIGHTS: dict[str, float] = {
    "hook": 1.6,
    "curiosity": 1.0,
    "standalone": 1.4,
    "emotion": 0.8,
    "payoff": 1.4,
    "information_density": 0.7,
    "visual_activity": 0.4,
    "speech_quality": 0.6,
    "story_completeness": 1.2,
    "novelty": 0.5,
    "captionability": 0.4,
    "pacing": 0.7,
    "shareability": 0.9,
    "rewatch": 0.6,
}

METRIC_LABELS = {
    "hook": "Hook Strength", "curiosity": "Curiosity", "standalone": "Standalone Context",
    "emotion": "Emotional Strength", "payoff": "Payoff", "information_density": "Information Density",
    "visual_activity": "Visual Activity", "speech_quality": "Speech Quality",
    "story_completeness": "Story Completeness", "novelty": "Novelty", "captionability": "Captionability",
    "pacing": "Pacing", "shareability": "Shareability", "rewatch": "Rewatch Potential",
    "dead_air": "Dead Air", "missing_context": "Missing Context", "abrupt_ending": "Abrupt Ending",
    "poor_audio": "Poor Audio", "low_visual_interest": "Low Visual Interest", "excessive_setup": "Excessive Setup",
    "repeated_topic": "Repeated Topic", "weak_opening": "Weak Opening", "sponsor_segment": "Sponsor / Ad Read",
}


@dataclass
class ScoreBreakdown:
    metrics: dict[str, float]
    penalties: dict[str, float]
    final: float

    def rows(self) -> list[tuple[str, float, str]]:
        return [(k, v, "score") for k, v in self.metrics.items()] + [(k, v, "penalty") for k, v in self.penalties.items()]


def combine(metrics: dict[str, float], penalties: dict[str, float], weights: dict[str, float] | None = None) -> ScoreBreakdown:
    """Weighted mean of available metrics (0-100) minus penalty points."""
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    num = den = 0.0
    for key, value in metrics.items():
        weight = w.get(key, 0.0)
        if weight <= 0:
            continue
        num += weight * value
        den += weight
    base = num / den if den else 0.0
    total_pen = sum(penalties.values())
    final = max(0.0, min(100.0, base - total_pen))
    return ScoreBreakdown(dict(metrics), dict(penalties), round(final, 2))


def normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    """Keep the total weight mass equal to the defaults so scores stay comparable over time."""
    target = sum(DEFAULT_WEIGHTS.values())
    total = sum(max(0.0, weights.get(k, 0.0)) for k in DEFAULT_WEIGHTS) or 1.0
    return {k: round(max(0.0, weights.get(k, 0.0)) * target / total, 4) for k in DEFAULT_WEIGHTS}
