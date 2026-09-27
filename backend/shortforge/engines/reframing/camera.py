"""Virtual camera path planning.

Offline processing lets us behave like a good camera operator instead of a jittery tracker:

1. Robustly clean the raw subject signal (gap filling + median filter).
2. Segment it into *holds*: the camera stays still while the subject remains inside a dead-zone.
3. Move between holds with eased (ease-in-out) pans that start slightly *before* the subject
   moves (we can look ahead), or with an instant cut when the jump is large (a whip-pan
   across the frame looks worse than a cut).
4. Run a critically-damped spring over the result to remove any residual velocity kinks and
   limit acceleration.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np


def ease_in_out(u: float) -> float:
    """Smootherstep: zero velocity *and* acceleration at both ends."""
    u = min(1.0, max(0.0, u))
    return u * u * u * (u * (u * 6 - 15) + 10)


def ease_out(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return 1 - (1 - u) ** 3


def ease_in(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return u**3


def fill_gaps(values: Sequence[float | None], default: float = 0.5) -> np.ndarray:
    """Linear interpolation over missing samples; hold at the ends."""
    arr = np.array([np.nan if v is None else v for v in values], dtype=np.float64)
    if len(arr) == 0:
        return arr
    ok = ~np.isnan(arr)
    if not ok.any():
        return np.full(len(arr), default)
    idx = np.arange(len(arr))
    return np.interp(idx, idx[ok], arr[ok])


def median_filter(x: np.ndarray, k: int = 5) -> np.ndarray:
    if len(x) < 3 or k < 3:
        return x.copy()
    k = min(k | 1, len(x) if len(x) % 2 else len(x) - 1)
    pad = k // 2
    padded = np.pad(x, pad, mode="edge")
    return np.array([np.median(padded[i : i + k]) for i in range(len(x))])


def plan_holds(t: np.ndarray, x: np.ndarray, *, deadzone: float, min_hold: float, settle: float = 0.5) -> list[tuple[float, float]]:
    """Return (time, position) keyframes where the camera settles on a new hold position.

    A move is triggered only when the subject stays outside the dead-zone for ``min_hold``
    seconds (ignores gestures and brief head turns).
    """
    if len(t) == 0:
        return []
    holds = [(float(t[0]), float(np.median(x[: max(1, int(np.searchsorted(t, t[0] + settle)))])))]
    pos = holds[0][1]
    outside_since: float | None = None
    for i in range(len(t)):
        if abs(x[i] - pos) > deadzone:
            if outside_since is None:
                outside_since = float(t[i])
            if t[i] - outside_since >= min_hold:
                j = int(np.searchsorted(t, t[i] + settle))
                window = x[i : max(i + 1, j)]
                pos = float(np.median(window))
                holds.append((outside_since, pos))
                outside_since = None
        else:
            outside_since = None
    return holds


def render_path(holds: list[tuple[float, float]], times: np.ndarray, *, cut_threshold: float,
                min_move: float = 0.35, max_move: float = 1.0, lead: float = 0.25) -> np.ndarray:
    """Evaluate a camera path at ``times`` from hold keyframes with eased transitions."""
    if not holds:
        return np.full(len(times), 0.5)
    out = np.empty(len(times))
    for i, tt in enumerate(times):
        pos = holds[0][1]
        for k in range(1, len(holds)):
            t_trig, target = holds[k]
            prev = holds[k - 1][1]
            dist = abs(target - prev)
            if dist >= cut_threshold:
                if tt >= t_trig:
                    pos = target
                continue
            dur = min(max_move, max(min_move, 0.3 + 1.6 * dist))
            t0 = t_trig - lead
            if tt >= t0 + dur:
                pos = target
            elif tt > t0:
                pos = prev + (target - prev) * ease_in_out((tt - t0) / dur)
        out[i] = pos
    return out


def spring_smooth(x: np.ndarray, dt: float, *, omega: float = 9.0, hard_cuts: Sequence[int] = ()) -> np.ndarray:
    """Critically-damped spring follower (zero-overshoot) with resets at hard-cut indices.

    Run forward and backward and average to cancel the phase lag of a causal filter.
    """
    if len(x) < 2:
        return x.copy()
    cuts = set(hard_cuts)

    def run(sig: np.ndarray, cut_set: set[int]) -> np.ndarray:
        y = np.empty_like(sig)
        pos, vel = sig[0], 0.0
        for i, target in enumerate(sig):
            if i in cut_set:
                pos, vel = target, 0.0
            else:
                # exact critically damped step
                delta = pos - target
                exp = math.exp(-omega * dt)
                new_delta = (delta + (vel + omega * delta) * dt) * exp
                vel = (vel - omega * (vel + omega * delta) * dt) * exp
                pos = target + new_delta
            y[i] = pos
        return y

    fwd = run(x, cuts)
    rev_cuts = {len(x) - i for i in cuts}
    bwd = run(x[::-1], rev_cuts)[::-1]
    return (fwd + bwd) / 2


def clamp_center(cx: float, crop_w_norm: float) -> float:
    half = min(0.5, crop_w_norm / 2)
    return min(1 - half, max(half, cx))


def catmull_rom(keys_t: Sequence[float], keys_v: Sequence[float], t: float) -> float:
    """Interpolate a smooth value through keyframes (clamped at the ends)."""
    n = len(keys_t)
    if n == 0:
        return 0.5
    if t <= keys_t[0]:
        return float(keys_v[0])
    if t >= keys_t[-1]:
        return float(keys_v[-1])
    i = int(np.searchsorted(keys_t, t)) - 1
    i = max(0, min(n - 2, i))
    t0, t1 = keys_t[i], keys_t[i + 1]
    u = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
    p0 = keys_v[max(0, i - 1)]
    p1, p2 = keys_v[i], keys_v[i + 1]
    p3 = keys_v[min(n - 1, i + 2)]
    # If the segment is a step (hard cut encoded by duplicate times), avoid overshoot.
    v = 0.5 * ((2 * p1) + (-p0 + p2) * u + (2 * p0 - 5 * p1 + 4 * p2 - p3) * u * u + (-p0 + 3 * p1 - 3 * p2 + p3) * u**3)
    lo, hi = min(p1, p2), max(p1, p2)
    return float(min(hi, max(lo, v)))


def jitter_score(values: Sequence[float], dt: float) -> float:
    """Mean absolute acceleration of a path (normalised units/s^2); used by QC."""
    if len(values) < 3:
        return 0.0
    v = np.asarray(values, dtype=np.float64)
    acc = np.diff(v, 2) / (dt * dt)
    return float(np.mean(np.abs(acc)))
