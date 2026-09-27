"""Targeted visual analysis of a time range (PASS 5) - only run on promising candidates."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from shortforge.engines.tracking.tracker import FaceTracker
from shortforge.engines.vision.faces import FaceDetector
from shortforge.engines.vision.sampler import sample_frames


@dataclass
class RangeVisuals:
    samples: int
    face_presence: float
    mean_faces: float
    max_faces: int
    main_face_size: float
    distinct_tracks: int
    screen_likeness: float
    sharpness: float
    motion: float
    detections: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("detections")
        return {k: round(v, 4) if isinstance(v, float) else v for k, v in d.items()}

    def visual_interest(self) -> float:
        """0-100: faces, motion and sharpness make a range watchable in vertical format."""
        face_term = 0.55 * min(1.0, self.face_presence) * min(1.0, self.main_face_size / 0.12)
        motion_term = 0.25 * min(1.0, self.motion / 0.05)
        sharp_term = 0.20 * min(1.0, self.sharpness / 300.0)
        return float(np.clip(100 * (face_term + motion_term + sharp_term) + 15, 0, 100))


def screen_score(frame: np.ndarray) -> float:
    """Heuristic 0..1: slides/screens/UI have many straight edges and flat regions."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 80, 160)
    density = edges.mean() / 255.0
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=60, minLineLength=gray.shape[1] // 8, maxLineGap=4)
    n_lines = 0 if lines is None else len(lines)
    flat = (np.abs(cv2.Laplacian(gray, cv2.CV_32F)) < 2).mean()
    return float(np.clip(0.4 * min(1.0, n_lines / 40) + 0.35 * min(1.0, density / 0.12) + 0.25 * flat, 0, 1))


def analyze_range(video: Path, start: float, end: float, detector: FaceDetector, *, fps: float = 2.0,
                  cuts: list[float] | None = None, width: int = 640) -> RangeVisuals:
    tracker = FaceTracker()
    cuts = sorted(cuts or [])
    cut_i = 0
    n = 0
    with_face = 0
    face_counts: list[int] = []
    sizes: list[float] = []
    screen: list[float] = []
    sharp: list[float] = []
    motion: list[float] = []
    prev_small = None
    detections: list[dict[str, Any]] = []
    for t, frame in sample_frames(video, start, end, fps, width=width):
        while cut_i < len(cuts) and cuts[cut_i] <= t:
            tracker.reset()
            cut_i += 1
        n += 1
        faces = detector.detect(frame)
        tracked = tracker.update(t, faces, frame)
        face_counts.append(len(faces))
        if faces:
            with_face += 1
            sizes.append(max(f.w for f in faces))
        for tid, f in tracked:
            detections.append({"t": round(t, 3), "x": f.x, "y": f.y, "w": f.w, "h": f.h, "score": f.score,
                               "track_id": tid})
        if n % 3 == 1:
            screen.append(screen_score(frame))
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        sharp.append(float(cv2.Laplacian(gray, cv2.CV_64F).var()))
        small = cv2.resize(gray, (64, 36), interpolation=cv2.INTER_AREA).astype(np.float32)
        if prev_small is not None:
            motion.append(float(np.abs(small - prev_small).mean()) / 255.0)
        prev_small = small
    if n == 0:
        return RangeVisuals(0, 0.0, 0.0, 0, 0.0, 0, 0.0, 0.0, 0.0)
    tracks = tracker.all_tracks(min_points=max(2, int(n * 0.15)))
    return RangeVisuals(
        samples=n,
        face_presence=with_face / n,
        mean_faces=float(np.mean(face_counts)),
        max_faces=int(max(face_counts)),
        main_face_size=float(np.median(sizes)) if sizes else 0.0,
        distinct_tracks=len(tracks),
        screen_likeness=float(np.mean(screen)) if screen else 0.0,
        sharpness=float(np.median(sharp)),
        motion=float(np.mean(motion)) if motion else 0.0,
        detections=detections,
    )
