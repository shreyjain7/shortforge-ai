"""Face detection (OpenCV YuNet ONNX, with a Haar-cascade fallback)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from shortforge.core.logging import get_logger

log = get_logger("vision.faces")


@dataclass
class FaceBox:
    """Normalised [0,1] coordinates relative to the frame."""

    x: float
    y: float
    w: float
    h: float
    score: float
    # Normalised landmarks: right eye, left eye, nose, right mouth corner, left mouth corner
    landmarks: tuple[tuple[float, float], ...] | None = None

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @property
    def area(self) -> float:
        return self.w * self.h

    def iou(self, other: FaceBox) -> float:
        ix = max(0.0, min(self.x + self.w, other.x + other.w) - max(self.x, other.x))
        iy = max(0.0, min(self.y + self.h, other.y + other.h) - max(self.y, other.y))
        inter = ix * iy
        union = self.area + other.area - inter
        return inter / union if union > 0 else 0.0

    def mouth_rect(self) -> tuple[float, float, float, float]:
        """Mouth region (normalised x, y, w, h)."""
        if self.landmarks and len(self.landmarks) >= 5:
            (rx, ry), (lx, ly) = self.landmarks[3], self.landmarks[4]
            mx, my = (rx + lx) / 2, (ry + ly) / 2
            mw = max(abs(lx - rx) * 1.6, self.w * 0.35)
            return mx - mw / 2, my - self.h * 0.12, mw, self.h * 0.26
        return self.x + self.w * 0.25, self.y + self.h * 0.62, self.w * 0.5, self.h * 0.3


class FaceDetector:
    def __init__(self, yunet_model: Path | None, score_threshold: float = 0.72) -> None:
        self.score_threshold = score_threshold
        self._yunet = None
        self._size: tuple[int, int] | None = None
        self._haar = None
        if yunet_model and yunet_model.exists():
            try:
                self._yunet = cv2.FaceDetectorYN.create(str(yunet_model), "", (320, 320), score_threshold, 0.3, 50)
            except cv2.error as exc:
                log.warning("YuNet failed to load (%s); falling back to Haar cascade", exc)
        if self._yunet is None:
            path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
            self._haar = cv2.CascadeClassifier(str(path))

    @property
    def backend(self) -> str:
        return "yunet" if self._yunet is not None else "haar"

    def detect(self, frame: np.ndarray, min_size: float = 0.035) -> list[FaceBox]:
        h, w = frame.shape[:2]
        boxes: list[FaceBox] = []
        if self._yunet is not None:
            if self._size != (w, h):
                self._yunet.setInputSize((w, h))
                self._size = (w, h)
            _, faces = self._yunet.detect(frame)
            if faces is not None:
                for f in faces:
                    x, y, bw, bh = (float(v) for v in f[:4])
                    lms = tuple((float(f[4 + 2 * k]) / w, float(f[5 + 2 * k]) / h) for k in range(5))
                    boxes.append(FaceBox(max(0.0, x / w), max(0.0, y / h), bw / w, bh / h, float(f[14]), lms))
        else:
            assert self._haar is not None
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            rects = self._haar.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6,
                                                minSize=(int(w * min_size), int(w * min_size)))
            for x, y, bw, bh in rects:
                boxes.append(FaceBox(x / w, y / h, bw / w, bh / h, 0.8))
        return [b for b in boxes if b.w >= min_size and b.score >= self.score_threshold]
