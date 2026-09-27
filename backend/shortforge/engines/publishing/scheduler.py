"""Upload slot planning: fixed daily slots, minimum-interval spacing, or queue order."""

from __future__ import annotations

from datetime import datetime, timedelta, tzinfo


def _local_tz() -> tzinfo:
    return datetime.now().astimezone().tzinfo  # type: ignore[return-value]


def _parse_slot(slot: str) -> tuple[int, int]:
    h, m = slot.strip().split(":")
    return int(h), int(m)


def _conflicts(t: datetime, taken: list[datetime], gap: timedelta) -> bool:
    return any(abs(t - x) < gap for x in taken)


def _count_on_day(day: datetime, taken: list[datetime], tz: tzinfo) -> int:
    d = day.astimezone(tz).date()
    return sum(1 for x in taken if x.astimezone(tz).date() == d)


def next_slot(now: datetime, *, strategy: str, slots: list[str], taken: list[datetime], max_per_day: int,
              min_gap_min: int, tz: tzinfo | None = None, horizon_days: int = 60) -> datetime:
    """Earliest valid publish time respecting daily limits and minimum spacing.

    ``now`` and ``taken`` must be timezone-aware. Slot times are interpreted in ``tz`` (local time
    by default), so "18:00" means 6 pm on the user's clock.
    """
    tz = tz or _local_tz()
    gap = timedelta(minutes=max(0, min_gap_min))
    earliest = now + timedelta(minutes=3)
    if strategy == "slots" and slots:
        parsed = sorted(_parse_slot(s) for s in slots)
        local_now = now.astimezone(tz)
        for day in range(horizon_days):
            base = (local_now + timedelta(days=day)).replace(hour=0, minute=0, second=0, microsecond=0)
            if max_per_day and _count_on_day(base, taken, tz) >= max_per_day:
                continue
            for h, m in parsed:
                cand = base.replace(hour=h, minute=m)
                if cand < earliest or _conflicts(cand, taken, gap):
                    continue
                return cand
        raise ValueError("No free upload slot in the scheduling horizon.")
    # interval / queue: as soon as possible, keeping the minimum spacing and daily cap
    cand = earliest
    later = [x for x in taken if x >= now - gap]
    if later:
        cand = max(cand, max(later) + gap)
    for _ in range(horizon_days * 24):
        if max_per_day and _count_on_day(cand, taken, tz) >= max_per_day:
            local = cand.astimezone(tz)
            cand = (local + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
            continue
        if _conflicts(cand, taken, gap):
            cand += gap
            continue
        return cand
    raise ValueError("No free upload time in the scheduling horizon.")
