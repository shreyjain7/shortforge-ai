"""Caption style presets (built-ins live in presets/captions/*.json; custom ones in the DB)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from shortforge.core.paths import assets_dir, presets_dir


class BoxStyle(BaseModel):
    enabled: bool = False
    color: str = "#000000"
    alpha: float = 0.55
    padding_x: int = 22
    padding_y: int = 12
    radius: int = 18
    blur: float = 0.0


class CaptionPreset(BaseModel):
    name: str
    description: str = ""
    font_file: str = "Montserrat-Black.ttf"
    font_name: str = "Montserrat Black"
    font_size: int = 84
    uppercase: bool = True
    primary_color: str = "#FFFFFF"
    highlight_color: str = "#FFE14D"
    emphasis_color: str = "#FFE14D"
    spoken_color: str | None = None
    outline_color: str = "#000000"
    outline_width: float = 6.0
    shadow_color: str = "#000000"
    shadow_alpha: float = 0.45
    shadow_depth: float = 3.0
    blur: float = 0.0
    """Edge blur; with a bright outline colour this produces a neon glow."""
    box: BoxStyle = Field(default_factory=BoxStyle)
    highlight_mode: Literal["color", "box", "karaoke", "none"] = "color"
    highlight_box_color: str = "#7C3AED"
    active_scale: float = 1.1
    emphasis_scale: float = 1.0
    animation: Literal["pop", "bounce", "fade", "slide", "none"] = "pop"
    position: float = 0.68
    max_words: int = 3
    max_lines: int = 2
    line_spacing: float = 1.08
    letter_spacing: float = 0.0
    speaker_colors: list[str] = Field(default_factory=list)
    hook_font_file: str = "Montserrat-ExtraBold.ttf"
    hook_font_name: str = "Montserrat ExtraBold"
    hook_text_color: str = "#111111"
    hook_box_color: str = "#FFFFFF"
    builtin: bool = False

    @property
    def font_path(self) -> Path:
        return fonts_dir() / self.font_file

    @property
    def hook_font_path(self) -> Path:
        return fonts_dir() / self.hook_font_file


def fonts_dir() -> Path:
    return assets_dir() / "fonts"


@lru_cache(maxsize=1)
def builtin_presets() -> dict[str, CaptionPreset]:
    out: dict[str, CaptionPreset] = {}
    folder = presets_dir() / "captions"
    for path in sorted(folder.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        preset = CaptionPreset.model_validate({**data, "builtin": True})
        out[preset.name] = preset
    return out


def get_preset(name: str, custom: dict[str, dict] | None = None) -> CaptionPreset:
    if custom and name in custom:
        return CaptionPreset.model_validate(custom[name])
    presets = builtin_presets()
    if name in presets:
        return presets[name]
    return presets.get("Bold") or next(iter(presets.values()))
