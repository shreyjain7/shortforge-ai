from datetime import UTC, datetime, timedelta, timezone

import pytest

from shortforge.engines.editing.timeline import EditTimeline, SourceRange
from shortforge.engines.learning.optimizer import Sample, pearson, update_weights
from shortforge.engines.publishing.scheduler import next_slot
from shortforge.engines.publishing.uploader import UploadRequest, build_body
from shortforge.engines.quality_control.qc import apply_repairs
from shortforge.engines.ranking.weights import DEFAULT_WEIGHTS

TZ = timezone(timedelta(hours=0))


def test_slot_strategy_picks_next_free_slot() -> None:
    now = datetime(2026, 9, 27, 9, 0, tzinfo=UTC)
    slots = ["10:00", "14:00", "18:00", "22:00"]
    first = next_slot(now, strategy="slots", slots=slots, taken=[], max_per_day=4, min_gap_min=90, tz=TZ)
    assert first == datetime(2026, 9, 27, 10, 0, tzinfo=TZ)
    second = next_slot(now, strategy="slots", slots=slots, taken=[first], max_per_day=4, min_gap_min=90, tz=TZ)
    assert second.hour == 14


def test_daily_cap_moves_to_next_day() -> None:
    now = datetime(2026, 9, 27, 9, 0, tzinfo=UTC)
    taken = [datetime(2026, 9, 27, h, 0, tzinfo=UTC) for h in (10, 14)]
    nxt = next_slot(now, strategy="slots", slots=["10:00", "14:00", "18:00"], taken=taken, max_per_day=2,
                    min_gap_min=60, tz=TZ)
    assert nxt == datetime(2026, 9, 28, 10, 0, tzinfo=TZ)


def test_interval_strategy_respects_gap() -> None:
    now = datetime(2026, 9, 27, 9, 0, tzinfo=UTC)
    taken = [now + timedelta(minutes=10)]
    nxt = next_slot(now, strategy="interval", slots=[], taken=taken, max_per_day=10, min_gap_min=90, tz=TZ)
    assert nxt - taken[0] >= timedelta(minutes=90)


def test_upload_body_scheduling() -> None:
    future = datetime.now(UTC) + timedelta(days=1)
    body = build_body(UploadRequest(path=None, title="<Hi>" * 40, description="d", tags=["#shorts", "tech"],  # type: ignore[arg-type]
                                    privacy="public", publish_at=future))
    assert body["status"]["privacyStatus"] == "private" and body["status"]["publishAt"].endswith("Z")
    assert "<" not in body["snippet"]["title"] and len(body["snippet"]["title"]) <= 100
    assert body["snippet"]["tags"] == ["shorts", "tech"]
    past = build_body(UploadRequest(path=None, title="t", description="", tags=[], privacy="unlisted",  # type: ignore[arg-type]
                                    publish_at=datetime.now(UTC) - timedelta(hours=1)))
    assert past["status"]["privacyStatus"] == "unlisted" and "publishAt" not in past["status"]


def _samples(n: int, signal: str = "hook") -> list[Sample]:
    import random

    rng = random.Random(1)
    out = []
    for i in range(n):
        hook = rng.uniform(30, 95)
        views = int(100 * (1 + (hook - 30) / 10) ** 2 * rng.uniform(0.8, 1.2))
        metrics = {k: rng.uniform(40, 80) for k in DEFAULT_WEIGHTS}
        metrics[signal] = hook
        out.append(Sample(i, metrics, {"duration": rng.uniform(15, 60), "hook_type": rng.choice(["question", "story"])},
                          views, views // 20, 96))
    return out


def test_learning_requires_minimum_samples() -> None:
    res = update_weights(_samples(5), dict(DEFAULT_WEIGHTS), min_samples=20)
    assert not res.updated and res.weights == DEFAULT_WEIGHTS


def test_learning_moves_weights_gradually_toward_evidence() -> None:
    res = update_weights(_samples(60), dict(DEFAULT_WEIGHTS), min_samples=20, learning_rate=0.15, max_change=0.2)
    assert res.updated
    assert res.correlations["hook"] > 0.5
    assert res.weights["hook"] > DEFAULT_WEIGHTS["hook"] * 1.0
    # never more than max_change per update (before mass renormalisation, allow small slack)
    for k, v in res.weights.items():
        assert v <= DEFAULT_WEIGHTS[k] * 1.25
    assert abs(sum(res.weights.values()) - sum(DEFAULT_WEIGHTS.values())) < 0.01


def test_pearson() -> None:
    assert pearson([1, 2, 3], [2, 4, 6]) == pytest.approx(1.0)
    assert pearson([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)
    assert pearson([1, 1, 1], [1, 2, 3]) == 0.0


def test_repairs_modify_timeline() -> None:
    tl = EditTimeline(source_path="x", source_width=1920, source_height=1080, ranges=[SourceRange(start=0, end=10)])
    fixed, notes = apply_repairs(tl, ["shrink_captions", "lower_gain"])
    assert fixed.captions.max_words == 2 and fixed.captions.overrides["font_scale"] < 1
    assert fixed.audio.true_peak == -3.5 and fixed.audio.target_lufs == -15.0 and notes
    assert tl.audio.true_peak == -1.5  # original untouched
    again, _ = apply_repairs(fixed, ["lower_gain"])
    assert again.audio.true_peak == -5.5  # progressive, so repeated attempts make progress
