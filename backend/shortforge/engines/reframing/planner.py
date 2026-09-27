"""Smart 9:16 reframing planner.

Per shot (scene-cut delimited) it decides a layout and a virtual-camera path:

* ``crop``  - follow the active speaker / main subject with a stable, eased camera;
* ``split`` - two persistent speakers far apart who both talk: top/bottom split;
* ``fit``   - screen recordings / slides / gameplay without faces: full frame over a blurred fill;
* motion-saliency crop for faceless footage that is not screen content.

Active speaker = face with the most mouth motion while the audio VAD reports speech, switched
with hysteresis and a minimum hold so the framing never flickers on small sounds.
"""

from __future__ import annotations

import itertools
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from shortforge.core.logging import get_logger
from shortforge.engines.editing.timeline import CropKey, EditTimeline, LayoutSegment, ZoomEvent
from shortforge.engines.reframing.camera import (
    clamp_center,
    fill_gaps,
    median_filter,
    plan_holds,
    render_path,
    spring_smooth,
)
from shortforge.engines.tracking.tracker import FaceTracker
from shortforge.engines.vision.analysis import screen_score
from shortforge.engines.vision.faces import FaceBox, FaceDetector
from shortforge.engines.vision.sampler import sample_frames

log = get_logger("reframe")

KEY_RATE = 10.0  # crop keys per output second


@dataclass
class ReframeConfig:
    mode: str = "auto"
    sample_fps: float = 6.0
    deadzone: float = 0.06
    min_hold: float = 1.2
    headroom: float = 0.38  # face centre at this fraction of crop height (when zoomed)
    punch_ins: bool = True
    max_punch_in: float = 1.12
    split_min_shot: float = 3.0


@dataclass
class Sample:
    t_src: float
    t_out: float
    faces: list[tuple[int, FaceBox, float]] = field(default_factory=list)  # (track, box, mouth activity)
    motion_x: float | None = None
    screen: float | None = None


def crop_width_norm(src_w: int, src_h: int, zoom: float = 1.0, aspect: float = 9 / 16) -> float:
    if src_w / src_h > aspect:
        return (src_h * aspect / zoom) / src_w
    return 1.0 / zoom


def _speech_at(speech: list[tuple[float, float]], t: float) -> bool:
    return any(s - 0.1 <= t <= e + 0.1 for s, e in speech)


def collect_samples(video: Path, timeline: EditTimeline, detector: FaceDetector, cuts_src: list[float],
                    cfg: ReframeConfig, analysis_width: int = 640) -> list[Sample]:
    tracker = FaceTracker()
    samples: list[Sample] = []
    cut_list = sorted(cuts_src)
    acc_out = 0.0
    prev_t = -1.0
    for r in timeline.ranges:
        prev_small = None
        for t_src, frame in sample_frames(video, r.start, r.end, cfg.sample_fps, width=analysis_width):
            if t_src > r.end:
                break
            if any(prev_t < c <= t_src for c in cut_list):
                tracker.reset()  # never carry a face track across a hard scene cut
                prev_small = None
            prev_t = t_src
            faces = detector.detect(frame)
            tracked = tracker.update(t_src, faces, frame)
            by_id = {tr.id: tr for tr in tracker.tracks}
            s = Sample(t_src, acc_out + (t_src - r.start),
                       [(tid, f, by_id[tid].last.mouth_activity if tid in by_id else 0.0) for tid, f in tracked])
            gray = cv2.cvtColor(cv2.resize(frame, (96, 54), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
            small = gray.astype(np.float32)
            if prev_small is not None:
                diff = np.abs(small - prev_small)
                col = diff.sum(axis=0)
                if col.sum() > 96 * 54 * 4:
                    s.motion_x = float((col * np.arange(96)).sum() / col.sum() / 96)
            prev_small = small
            if len(samples) % 6 == 0:
                s.screen = screen_score(frame)
            samples.append(s)
        acc_out += r.duration
    return samples


def split_shots(samples: list[Sample], cuts_src: list[float]) -> list[list[Sample]]:
    shots: list[list[Sample]] = []
    cur: list[Sample] = []
    for s in samples:
        if cur and any(cur[-1].t_src < c <= s.t_src for c in cuts_src):
            shots.append(cur)
            cur = []
        cur.append(s)
    if cur:
        shots.append(cur)
    return shots


def choose_speaker_series(shot: list[Sample], speech: list[tuple[float, float]], min_hold: float,
                          sample_fps: float) -> list[int | None]:
    """Track id to follow per sample: active speaker with hysteresis and minimum hold."""
    size_by_track: dict[int, list[float]] = defaultdict(list)
    for s in shot:
        for tid, box, _ in s.faces:
            size_by_track[tid].append(box.w)
    if not size_by_track:
        return [None] * len(shot)
    # Primary fallback: largest, most persistent face.
    dominant = max(size_by_track, key=lambda k: np.mean(size_by_track[k]) * len(size_by_track[k]))
    # Smoothed per-track activity (1 s window).
    win = max(1, int(sample_fps))
    series: list[int | None] = []
    current = dominant
    challenger: int | None = None
    challenger_since = 0.0
    hist: dict[int, list[float]] = defaultdict(list)
    for s in shot:
        present = {tid: act for tid, _, act in s.faces}
        speaking = _speech_at(speech, s.t_src)
        for tid in size_by_track:
            hist[tid].append(present.get(tid, 0.0) if speaking else 0.0)
        scores = {tid: float(np.mean(hist[tid][-win:])) for tid in present}
        if current not in present and present:
            # Current subject left the frame: pick the best visible one immediately.
            current = max(present, key=lambda k: (scores.get(k, 0.0), next(b.w for t, b, _ in s.faces if t == k)))
            challenger = None
        elif present and speaking:
            best = max(scores, key=scores.get)
            cur_score = scores.get(current, 0.0)
            if best != current and scores[best] > 1.5 * cur_score + 0.004:
                if challenger != best:
                    challenger, challenger_since = best, s.t_out
                elif s.t_out - challenger_since >= min_hold * 0.6:
                    current, challenger = best, None
            else:
                challenger = None
        series.append(current if present else None)
    return series


def _face_for(sample: Sample, tid: int | None) -> FaceBox | None:
    if tid is None:
        return None
    for t, box, _ in sample.faces:
        if t == tid:
            return box
    return None


def decide_layout(shot: list[Sample], speech: list[tuple[float, float]], cfg: ReframeConfig, crop_w: float) -> str:
    n = len(shot)
    presence = sum(1 for s in shot if s.faces) / max(1, n)
    screens = [s.screen for s in shot if s.screen is not None]
    screen = float(np.mean(screens)) if screens else 0.0
    max_face = max((b.w for s in shot for _, b, _ in s.faces), default=0.0)
    duration = shot[-1].t_out - shot[0].t_out if n > 1 else 0.0
    if cfg.mode in ("presentation", "tech", "gameplay") and (presence < 0.5 or max_face < 0.07):
        return "fit"
    if presence < 0.3:
        # Slides/screen recordings are mostly static; action/POV footage with many straight lines is not.
        moving = sum(1 for s in shot if s.motion_x is not None) / max(1, n)
        if cfg.mode in ("presentation", "tech", "gameplay"):
            return "fit"
        return "fit" if screen > 0.42 and moving < 0.5 else "crop"
    if cfg.mode in ("auto", "conversation", "podcast") and duration >= cfg.split_min_shot:
        # Two persistent, far-apart faces that both speak -> split screen.
        tracks: dict[int, list[tuple[float, float]]] = defaultdict(list)
        for s in shot:
            sp = _speech_at(speech, s.t_src)
            for tid, box, act in s.faces:
                tracks[tid].append((box.cx, act if sp else 0.0))
        persistent = [(tid, v) for tid, v in tracks.items() if len(v) >= 0.6 * n]
        if len(persistent) == 2:
            (_, a), (_, b) = persistent
            ax, bx = np.mean([p[0] for p in a]), np.mean([p[0] for p in b])
            act_a, act_b = sum(p[1] for p in a), sum(p[1] for p in b)
            total = act_a + act_b
            both_talk = total > 0 and min(act_a, act_b) / total > 0.25
            if abs(ax - bx) > crop_w * 1.05 and both_talk:
                return "split"
    return "crop"


def _path_from_targets(times: np.ndarray, targets: list[float | None], cfg: ReframeConfig, crop_w: float,
                       default: float = 0.5) -> np.ndarray:
    x = fill_gaps(targets, default)
    x = median_filter(x, 5)
    x = np.array([clamp_center(v, crop_w) for v in x])
    holds = plan_holds(times, x, deadzone=cfg.deadzone * max(0.6, crop_w / 0.32), min_hold=cfg.min_hold * 0.5)
    path = render_path(holds, times, cut_threshold=crop_w * 0.9)
    jumps = [i for i in range(1, len(path)) if abs(path[i] - path[i - 1]) > crop_w * 0.5]
    dt = float(np.median(np.diff(times))) if len(times) > 1 else 0.1
    path = spring_smooth(path, dt, omega=7.0, hard_cuts=jumps)
    return np.array([clamp_center(v, crop_w) for v in path])


def _resample(times: np.ndarray, values: np.ndarray, t0: float, t1: float) -> list[tuple[float, float]]:
    out = []
    n = max(2, int((t1 - t0) * KEY_RATE) + 1)
    for k in range(n):
        t = t0 + (t1 - t0) * k / (n - 1)
        out.append((round(t, 3), float(np.interp(t, times, values))))
    return out


def plan_reframe(video: Path, timeline: EditTimeline, detector: FaceDetector, cuts_src: list[float],
                 speech: list[tuple[float, float]], cfg: ReframeConfig) -> tuple[list[LayoutSegment], list[CropKey], dict]:
    src_w, src_h = timeline.source_width, timeline.source_height
    crop_w = crop_width_norm(src_w, src_h)
    samples = collect_samples(video, timeline, detector, cuts_src, cfg)
    total = timeline.duration
    if not samples:
        return ([LayoutSegment(start=0, end=total, layout="crop")],
                [CropKey(t=0, cx=0.5), CropKey(t=total, cx=0.5)], {"samples": 0})
    shots = split_shots(samples, cuts_src)
    layouts: list[LayoutSegment] = []
    keys: list[CropKey] = []
    stats = {"samples": len(samples), "shots": len(shots), "layouts": defaultdict(float), "speaker_switches": 0}
    for si, shot in enumerate(shots):
        t0 = 0.0 if si == 0 else shot[0].t_out
        t1 = total if si == len(shots) - 1 else shots[si + 1][0].t_out
        times = np.array([s.t_out for s in shot])
        layout = decide_layout(shot, speech, cfg, crop_w)
        seg = LayoutSegment(start=round(t0, 3), end=round(t1, 3), layout=layout)
        if layout == "split":
            tracks: dict[int, list[float]] = defaultdict(list)
            for s in shot:
                for tid, box, _ in s.faces:
                    tracks[tid].append(box.cx)
            persistent = sorted((tid for tid, v in tracks.items() if len(v) >= 0.6 * len(shot)),
                                key=lambda k: np.mean(tracks[k]))[:2]
            half_w = crop_width_norm(src_w, src_h, aspect=9 / 8) * 1.0 / 1.35
            paths = []
            for tid in persistent:
                tx = [(_face_for(s, tid).cx if _face_for(s, tid) else None) for s in shot]
                ty = [(_face_for(s, tid).cy if _face_for(s, tid) else None) for s in shot]
                px = _path_from_targets(times, tx, cfg, half_w)
                py = median_filter(fill_gaps(ty, 0.45), 7)
                paths.append((px, py))
            (ax, ay), (bx, by) = paths
            for t, v in _resample(times, ax, t0, t1):
                keys.append(CropKey(t=t, cx=v, cy=float(np.interp(t, times, ay)), zoom=1.35))
            seg.secondary = [CropKey(t=t, cx=v, cy=float(np.interp(t, times, by)), zoom=1.35)
                             for t, v in _resample(times, bx, t0, t1)]
            seg.note = "two active speakers"
        elif layout == "fit":
            keys.append(CropKey(t=round(t0, 3), cx=0.5))
            keys.append(CropKey(t=round(t1, 3), cx=0.5))
            seg.note = "screen/slide content kept whole"
        else:
            series = choose_speaker_series(shot, speech, cfg.min_hold, cfg.sample_fps)
            stats["speaker_switches"] += sum(1 for a, b in itertools.pairwise(series)
                                             if a is not None and b is not None and a != b)
            targets: list[float | None] = []
            for s, tid in zip(shot, series, strict=False):
                box = _face_for(s, tid)
                if box is not None:
                    # Look-room: nudge framing slightly toward the side the face is on so it isn't
                    # rigidly centred (natural composition), bounded to a small offset.
                    targets.append(box.cx + 0.08 * crop_w * (0.5 - box.cx))
                else:
                    targets.append(s.motion_x if not any(x.faces for x in shot) else None)
            default = float(np.nanmean([v for v in targets if v is not None])) if any(v is not None for v in targets) else 0.5
            path = _path_from_targets(times, targets, cfg, crop_w, default)
            cy_vals = [(_face_for(s, tid).cy if _face_for(s, tid) else None) for s, tid in zip(shot, series, strict=False)]
            cy_path = median_filter(fill_gaps(cy_vals, 0.42), 9)
            for t, v in _resample(times, path, t0, t1):
                cy = float(np.interp(t, times, cy_path))
                keys.append(CropKey(t=t, cx=v, cy=cy))
            seg.note = "speaker framing" if any(s.faces for s in shot) else "motion framing"
        stats["layouts"][layout] += t1 - t0
        layouts.append(seg)
    stats["layouts"] = {k: round(v, 2) for k, v in stats["layouts"].items()}
    return layouts, keys, stats


def plan_punch_ins(timeline: EditTimeline, emphasis_times: list[float], cfg: ReframeConfig,
                   source_height: int) -> list[ZoomEvent]:
    """Tasteful punch-ins: alternate framing across jump cuts (hides the cut) and push in on the
    strongest emphasis moments. Never more than one push-in every ~6 s; limited by source resolution."""
    if not cfg.punch_ins:
        return []
    # Upscale headroom: a 1080p source cropped to 9:16 is already ~1.78x upscaled.
    res_factor = source_height / 1080.0
    max_scale = min(cfg.max_punch_in, 1.08 + 0.06 * max(0.0, res_factor - 1.0) + 0.04)
    crop_segments = [(s.start, s.end) for s in timeline.layouts if s.layout == "crop"] or [(0.0, timeline.duration)]

    def in_crop(t: float) -> bool:
        return any(a <= t <= b for a, b in crop_segments)

    events: list[ZoomEvent] = []
    cuts = timeline.cut_points()
    zoomed = False
    for i, c in enumerate(cuts):
        nxt = cuts[i + 1] if i + 1 < len(cuts) else timeline.duration
        zoomed = not zoomed
        if zoomed and in_crop(c) and nxt - c > 0.8:
            events.append(ZoomEvent(start=c, end=nxt, scale=min(max_scale, 1.06), ease_in=0.0, ease_out=0.0,
                                    reason="jump-cut punch"))
    last = -10.0
    for t in sorted(emphasis_times):
        if t - last < 6.0 or not in_crop(t) or t > timeline.duration - 1.5:
            continue
        if any(e.start <= t <= e.end for e in events):
            continue
        end = min(timeline.duration, t + 2.2)
        events.append(ZoomEvent(start=max(0.0, t - 0.15), end=end, scale=max_scale, ease_in=0.45, ease_out=0.5,
                                reason="emphasis"))
        last = t
    # Global density cap: at most ~1 punch-in per 8 s and >= 3 s apart; jump-cut punches (which hide
    # cuts) take priority over emphasis push-ins.
    budget = max(1, int(timeline.duration / 8))
    ranked = sorted(events, key=lambda e: (e.reason != "jump-cut punch", e.start))
    kept: list[ZoomEvent] = []
    for e in ranked:
        if len(kept) >= budget:
            break
        if all(e.start >= k.end + 3.0 or e.end <= k.start - 3.0 for k in kept):
            kept.append(e)
    kept.sort(key=lambda e: e.start)
    return kept
