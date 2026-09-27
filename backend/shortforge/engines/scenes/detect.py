"""Scene-boundary detection (PySceneDetect) plus a per-second visual-activity signal.

Runs on the low-resolution proxy so it is cheap. The same decode pass also measures frame-to-frame
change, which the clip scorer uses as "visual activity" without decoding the video again.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from shortforge.core.logging import get_logger
from shortforge.engines.media.ffmpeg import CancelToken

log = get_logger("scenes")


@dataclass
class SceneResult:
    scenes: list[tuple[float, float]]
    cuts: list[float]
    activity: np.ndarray  # mean abs frame difference per second, 0..1
    fps: float
    duration: float


def detect_scenes(video_path: Path, *, threshold: float = 27.0, min_scene_len_s: float = 0.6,
                  progress: Callable[[float, str], None] | None = None,
                  cancel: CancelToken | None = None) -> SceneResult:
    from scenedetect import ContentDetector, SceneManager, open_video

    video = open_video(str(video_path))
    fps = float(video.frame_rate)
    duration = float(video.duration.seconds) if video.duration is not None else 0.0
    total_frames = max(1, int(duration * fps))
    activity_sum = np.zeros(int(duration) + 2, dtype=np.float64)
    activity_cnt = np.zeros_like(activity_sum)
    state = {"prev": None, "n": 0}

    class ProgressContentDetector(ContentDetector):
        def process_frame(self, timecode, frame_img):  # type: ignore[no-untyped-def]
            n = state["n"]
            state["n"] = n + 1
            if cancel is not None and n % 60 == 0:
                cancel.raise_if_cancelled()
            small = cv2.resize(frame_img, (64, 36), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
            if state["prev"] is not None:
                sec = min(int(n / fps), len(activity_sum) - 1)
                activity_sum[sec] += float(np.abs(gray - state["prev"]).mean()) / 255.0
                activity_cnt[sec] += 1
            state["prev"] = gray
            if progress and n % 300 == 0:
                progress(min(0.99, n / total_frames), "Detecting scenes")
            return super().process_frame(timecode, frame_img)

    manager = SceneManager()
    manager.add_detector(ProgressContentDetector(threshold=threshold, min_scene_len=max(1, int(min_scene_len_s * fps))))
    manager.auto_downscale = True
    manager.detect_scenes(video=video)
    scene_list = manager.get_scene_list(start_in_scene=True)
    scenes = [(round(s.seconds, 3), round(e.seconds, 3)) for s, e in scene_list]
    if not scenes and duration:
        scenes = [(0.0, duration)]
    cuts = [s for s, _ in scenes[1:]]
    activity = np.divide(activity_sum, np.maximum(activity_cnt, 1))
    # Normalise: a mean abs diff of ~0.08 already means lots of motion.
    activity = np.clip(activity / 0.08, 0, 1).astype(np.float32)
    if progress:
        progress(1.0, "Scenes detected")
    log.info("detected %d scenes in %s", len(scenes), video_path.name)
    return SceneResult(scenes, cuts, activity, fps, duration)


def nearest_cut(cuts: list[float], t: float, window: float) -> float | None:
    best = None
    for c in cuts:
        if abs(c - t) <= window and (best is None or abs(c - t) < abs(best - t)):
            best = c
    return best


def cuts_in_range(cuts: list[float], start: float, end: float) -> list[float]:
    return [c for c in cuts if start < c < end]
