"""Face tracking across sampled frames plus visual speaking-activity estimation.

Faces are associated frame-to-frame by IoU (falling back to centre distance), producing stable
track ids. For each tracked face we measure motion energy inside the mouth region between
consecutive samples; combined with the audio VAD this identifies the active speaker.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from shortforge.engines.vision.faces import FaceBox


@dataclass
class TrackPoint:
    t: float
    box: FaceBox
    mouth_activity: float = 0.0


@dataclass
class Track:
    id: int
    points: list[TrackPoint] = field(default_factory=list)
    misses: int = 0

    @property
    def last(self) -> TrackPoint:
        return self.points[-1]

    def duration(self) -> float:
        return self.points[-1].t - self.points[0].t if len(self.points) > 1 else 0.0

    def mean_box(self) -> tuple[float, float, float]:
        cx = float(np.mean([p.box.cx for p in self.points]))
        cy = float(np.mean([p.box.cy for p in self.points]))
        size = float(np.mean([p.box.w for p in self.points]))
        return cx, cy, size

    def activity(self) -> float:
        vals = [p.mouth_activity for p in self.points if p.mouth_activity > 0]
        return float(np.mean(vals)) if vals else 0.0


class FaceTracker:
    def __init__(self, iou_threshold: float = 0.25, max_center_dist: float = 0.12, max_misses: int = 4) -> None:
        self.iou_threshold = iou_threshold
        self.max_center_dist = max_center_dist
        self.max_misses = max_misses
        self.tracks: list[Track] = []
        self._finished: list[Track] = []
        self._next_id = 0
        self._prev_gray: np.ndarray | None = None

    def reset(self) -> None:
        """Call at hard scene cuts: tracks never continue across a cut."""
        self._finished.extend(self.tracks)
        self.tracks = []
        self._prev_gray = None

    def update(self, t: float, faces: list[FaceBox], frame: np.ndarray | None = None) -> list[tuple[int, FaceBox]]:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame is not None else None
        assigned: dict[int, int] = {}
        # Greedy association by best IoU / distance.
        pairs = []
        for ti, tr in enumerate(self.tracks):
            for fi, f in enumerate(faces):
                iou = tr.last.box.iou(f)
                dist = float(np.hypot(tr.last.box.cx - f.cx, tr.last.box.cy - f.cy))
                if iou >= self.iou_threshold or dist <= self.max_center_dist:
                    pairs.append((iou - dist, ti, fi))
        used_t: set[int] = set()
        used_f: set[int] = set()
        for _, ti, fi in sorted(pairs, reverse=True):
            if ti in used_t or fi in used_f:
                continue
            used_t.add(ti)
            used_f.add(fi)
            assigned[fi] = ti
        result: list[tuple[int, FaceBox]] = []
        for fi, f in enumerate(faces):
            activity = 0.0
            if gray is not None and self._prev_gray is not None and self._prev_gray.shape == gray.shape:
                activity = self._mouth_motion(gray, self._prev_gray, f)
            if fi in assigned:
                tr = self.tracks[assigned[fi]]
                tr.misses = 0
            else:
                tr = Track(self._next_id)
                self._next_id += 1
                self.tracks.append(tr)
            tr.points.append(TrackPoint(t, f, activity))
            result.append((tr.id, f))
        for ti, tr in enumerate(self.tracks):
            if ti not in used_t and tr.points[-1].t != t:
                tr.misses += 1
        alive = []
        for tr in self.tracks:
            if tr.misses > self.max_misses:
                self._finished.append(tr)
            else:
                alive.append(tr)
        self.tracks = alive
        self._prev_gray = gray
        return result

    @staticmethod
    def _mouth_motion(gray: np.ndarray, prev: np.ndarray, face: FaceBox) -> float:
        h, w = gray.shape
        mx, my, mw, mh = face.mouth_rect()
        x0, y0 = max(0, int(mx * w)), max(0, int(my * h))
        x1, y1 = min(w, int((mx + mw) * w)), min(h, int((my + mh) * h))
        if x1 - x0 < 4 or y1 - y0 < 3:
            return 0.0
        a = gray[y0:y1, x0:x1].astype(np.float32)
        b = prev[y0:y1, x0:x1].astype(np.float32)
        # Normalise by whole-face motion so head movement is not mistaken for speech.
        fx0, fy0 = max(0, int(face.x * w)), max(0, int(face.y * h))
        fx1, fy1 = min(w, int((face.x + face.w) * w)), min(h, int((face.y + face.h * 0.55) * h))
        head = float(np.abs(gray[fy0:fy1, fx0:fx1].astype(np.float32) - prev[fy0:fy1, fx0:fx1]).mean()) \
            if fx1 > fx0 and fy1 > fy0 else 0.0
        mouth = float(np.abs(a - b).mean())
        return max(0.0, mouth - 0.7 * head) / 255.0

    def all_tracks(self, min_points: int = 2) -> list[Track]:
        return [t for t in self._finished + self.tracks if len(t.points) >= min_points]
