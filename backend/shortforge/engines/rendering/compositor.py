"""Per-frame compositing of the vertical output (crop / split / fit layouts, punch-in zoom).

Crops use a sub-pixel affine warp, so slow camera moves glide instead of stepping pixel by pixel.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass

import cv2
import numpy as np

from shortforge.engines.editing.timeline import CropKey, EditTimeline, LayoutSegment, ZoomEvent
from shortforge.engines.reframing.camera import catmull_rom, ease_in_out


@dataclass
class CropTrack:
    t: list[float]
    cx: list[float]
    cy: list[float]
    zoom: list[float]

    @classmethod
    def from_keys(cls, keys: list[CropKey]) -> CropTrack:
        keys = sorted(keys, key=lambda k: k.t)
        return cls([k.t for k in keys], [k.cx for k in keys], [k.cy for k in keys], [k.zoom for k in keys])

    def at(self, t: float) -> tuple[float, float, float]:
        if not self.t:
            return 0.5, 0.5, 1.0
        # Hard cuts are encoded as two keys very close in time; interpolate linearly there.
        i = bisect.bisect_left(self.t, t)
        if 0 < i < len(self.t) and self.t[i] - self.t[i - 1] < 0.02:
            return self.cx[i], self.cy[i], self.zoom[i]
        return (catmull_rom(self.t, self.cx, t), catmull_rom(self.t, self.cy, t),
                float(np.interp(t, self.t, self.zoom)))


def zoom_at(events: list[ZoomEvent], t: float) -> float:
    z = 1.0
    for e in events:
        if not e.start <= t <= e.end:
            continue
        if e.ease_in > 0 and t < e.start + e.ease_in:
            f = ease_in_out((t - e.start) / e.ease_in)
        elif e.ease_out > 0 and t > e.end - e.ease_out:
            f = ease_in_out((e.end - t) / e.ease_out)
        else:
            f = 1.0
        z *= 1.0 + (e.scale - 1.0) * f
    return z


def layout_at(layouts: list[LayoutSegment], t: float) -> LayoutSegment | None:
    for seg in layouts:
        if seg.start <= t < seg.end:
            return seg
    return layouts[-1] if layouts else None


def crop_rect(src_w: int, src_h: int, cx: float, cy: float, zoom: float, aspect: float) -> tuple[float, float, float, float]:
    """(x0, y0, w, h) in source pixels for a crop of the given output aspect (w/h)."""
    if src_w / src_h > aspect:
        h = src_h / zoom
        w = h * aspect
    else:
        w = src_w / zoom
        h = w / aspect
    x0 = min(max(cx * src_w - w / 2, 0.0), src_w - w)
    y0 = min(max(cy * src_h - h / 2, 0.0), src_h - h)
    return x0, y0, w, h


def warp_crop(frame: np.ndarray, rect: tuple[float, float, float, float], out_w: int, out_h: int,
              interp: int) -> np.ndarray:
    x0, y0, w, h = rect
    sx, sy = out_w / w, out_h / h
    m = np.array([[sx, 0, -x0 * sx], [0, sy, -y0 * sy]], dtype=np.float64)
    return cv2.warpAffine(frame, m, (out_w, out_h), flags=interp, borderMode=cv2.BORDER_REPLICATE)


class Compositor:
    def __init__(self, timeline: EditTimeline, *, quality: str = "BALANCED") -> None:
        self.tl = timeline
        self.W, self.H = timeline.width, timeline.height
        self.track = CropTrack.from_keys(timeline.crop)
        self.secondary: dict[int, CropTrack] = {
            i: CropTrack.from_keys(seg.secondary) for i, seg in enumerate(timeline.layouts) if seg.secondary
        }
        self.interp = {"FAST": cv2.INTER_LINEAR, "BALANCED": cv2.INTER_CUBIC, "ULTRA": cv2.INTER_LANCZOS4}.get(
            quality, cv2.INTER_CUBIC)
        self.headroom_cy: float | None = None
        self.last_rect: tuple[float, float, float, float] | None = None

    def _crop(self, frame: np.ndarray, t: float, track: CropTrack, out_w: int, out_h: int,
              extra_zoom: float = 1.0) -> np.ndarray:
        src_h, src_w = frame.shape[:2]
        cx, cy, z = track.at(t)
        zoom = max(1.0, z * extra_zoom)
        if zoom > 1.0:
            # When zoomed, place the subject's face in the upper third (headroom) rather than centred.
            _, _, _, h_px = crop_rect(src_w, src_h, cx, cy, zoom, out_w / out_h)
            cy = cy + (0.5 - 0.38) * (h_px / src_h)
        else:
            cy = 0.5
        rect = crop_rect(src_w, src_h, cx, cy, zoom, out_w / out_h)
        self.last_rect = rect
        return warp_crop(frame, rect, out_w, out_h, self.interp)

    def _fit(self, frame: np.ndarray) -> np.ndarray:
        src_h, src_w = frame.shape[:2]
        # Background: cover-scaled, heavily blurred and darkened copy (cheap: blur at low resolution).
        scale_bg = max(self.W / src_w, self.H / src_h)
        small = cv2.resize(frame, (max(2, int(src_w * scale_bg / 8)), max(2, int(src_h * scale_bg / 8))),
                           interpolation=cv2.INTER_AREA)
        small = cv2.GaussianBlur(small, (0, 0), 6)
        bg = cv2.resize(small, (int(src_w * scale_bg) + 2, int(src_h * scale_bg) + 2), interpolation=cv2.INTER_LINEAR)
        oy, ox = (bg.shape[0] - self.H) // 2, (bg.shape[1] - self.W) // 2
        out = (bg[oy : oy + self.H, ox : ox + self.W].astype(np.float32) * 0.55).astype(np.uint8)
        # Foreground: full frame fitted to width, slightly above centre to leave room for captions.
        fw = self.W
        fh = round(src_h * fw / src_w)
        if fh > self.H:
            fh = self.H
            fw = round(src_w * fh / src_h)
        fg = cv2.resize(frame, (fw, fh), interpolation=cv2.INTER_AREA if fw < src_w else cv2.INTER_CUBIC)
        y = int(max(0, min(self.H - fh, self.H * 0.42 - fh / 2)))
        x = (self.W - fw) // 2
        out[y : y + fh, x : x + fw] = fg
        self.last_rect = (0.0, 0.0, float(src_w), float(src_h))
        return out

    def _split(self, frame: np.ndarray, t: float, seg_index: int) -> np.ndarray:
        half = self.H // 2
        top = self._crop(frame, t, self.track, self.W, half)
        sec = self.secondary.get(seg_index)
        bottom = self._crop(frame, t, sec, self.W, self.H - half) if sec else top
        out = np.vstack([top, bottom])
        out[half - 2 : half + 2, :] = (18, 18, 18)
        return out

    def compose(self, frame: np.ndarray, t: float) -> np.ndarray:
        seg = layout_at(self.tl.layouts, t)
        kind = seg.layout if seg else "crop"
        if kind == "fit":
            return self._fit(frame)
        if kind == "split" and seg is not None:
            return self._split(frame, t, self.tl.layouts.index(seg))
        return self._crop(frame, t, self.track, self.W, self.H, zoom_at(self.tl.zooms, t))
