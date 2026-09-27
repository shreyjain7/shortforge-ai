"""Non-destructive edit timeline: the single source of truth a Short is rendered from.

All times in ``ranges`` are *source* seconds; every other time is *output* seconds (after cuts).
The automatic editor produces a timeline; the manual editor modifies it; the renderer consumes it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

LayoutKind = Literal["crop", "fit", "split", "pip"]


class SourceRange(BaseModel):
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


class CropKey(BaseModel):
    """Virtual-camera sample on the output timeline. cx/cy are normalised source coordinates of the
    crop centre; zoom >= 1 narrows the crop (punch-in) around that centre."""

    t: float
    cx: float
    cy: float = 0.5
    zoom: float = 1.0


class LayoutSegment(BaseModel):
    start: float
    end: float
    layout: LayoutKind = "crop"
    # split: secondary crop track (e.g. the second speaker); pip: presenter crop
    secondary: list[CropKey] = Field(default_factory=list)
    note: str | None = None


class ZoomEvent(BaseModel):
    start: float
    end: float
    scale: float = 1.08
    ease_in: float = 0.35
    ease_out: float = 0.35
    reason: str | None = None


class CaptionWord(BaseModel):
    text: str
    start: float
    end: float
    emphasis: bool = False
    speaker: int | None = None
    src_start: float | None = None
    """Source-time anchor so manual range edits can re-time captions exactly."""
    src_end: float | None = None


class CaptionTrack(BaseModel):
    enabled: bool = True
    preset: str = "Bold"
    words: list[CaptionWord] = Field(default_factory=list)
    max_words: int | None = None
    vertical_position: float | None = None
    overrides: dict = Field(default_factory=dict)


class TextOverlay(BaseModel):
    text: str
    start: float
    end: float
    kind: Literal["hook", "text"] = "hook"


class BrollInsert(BaseModel):
    path: str
    start: float
    end: float
    source_start: float = 0.0
    mode: Literal["full", "pip"] = "full"
    label: str | None = None


class MusicTrack(BaseModel):
    path: str
    volume_db: float = -22.0
    duck_db: float = -10.0
    offset: float = 0.0
    fade_in: float = 0.6
    fade_out: float = 1.2


class AudioSpec(BaseModel):
    target_lufs: float = -14.0
    true_peak: float = -1.5
    fade_ms: int = 40
    compressor: bool = False
    eq: bool = False
    denoise: bool = False
    gain_db: float = 0.0


class EnhanceSpec(BaseModel):
    sharpen: bool = False
    vignette: bool = False
    contrast: bool = False
    color: bool = False
    denoise: bool = False


class SafeArea(BaseModel):
    top: float = 0.08
    bottom: float = 0.22
    left: float = 0.06
    right: float = 0.14


class EditTimeline(BaseModel):
    schema_version: int = 1
    source_path: str
    source_width: int
    source_height: int
    fps: float = 30.0
    width: int = 1080
    height: int = 1920
    ranges: list[SourceRange]
    layouts: list[LayoutSegment] = Field(default_factory=list)
    crop: list[CropKey] = Field(default_factory=list)
    zooms: list[ZoomEvent] = Field(default_factory=list)
    captions: CaptionTrack = Field(default_factory=CaptionTrack)
    overlays: list[TextOverlay] = Field(default_factory=list)
    broll: list[BrollInsert] = Field(default_factory=list)
    broll_suggestions: list[BrollInsert] = Field(default_factory=list)
    music: MusicTrack | None = None
    audio: AudioSpec = Field(default_factory=AudioSpec)
    enhance: EnhanceSpec = Field(default_factory=EnhanceSpec)
    safe_area: SafeArea = Field(default_factory=SafeArea)
    speed: float = 1.0
    notes: list[str] = Field(default_factory=list)

    @property
    def duration(self) -> float:
        return sum(r.duration for r in self.ranges)

    def source_to_output(self, t_src: float) -> float | None:
        """Map a source timestamp to the output timeline (None if it was cut)."""
        acc = 0.0
        for r in self.ranges:
            if r.start <= t_src <= r.end:
                return acc + (t_src - r.start)
            acc += r.duration
        return None

    def output_to_source(self, t_out: float) -> float:
        acc = 0.0
        for r in self.ranges:
            if t_out <= acc + r.duration:
                return r.start + (t_out - acc)
            acc += r.duration
        return self.ranges[-1].end if self.ranges else 0.0

    def cut_points(self) -> list[float]:
        """Output times where a jump cut happens."""
        pts, acc = [], 0.0
        for r in self.ranges[:-1]:
            acc += r.duration
            pts.append(acc)
        return pts
