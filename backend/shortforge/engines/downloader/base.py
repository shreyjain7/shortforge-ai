"""Downloader interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from shortforge.engines.media.ffmpeg import CancelToken


@dataclass
class DownloadProgress:
    downloaded_bytes: int
    total_bytes: int | None
    speed_bps: float | None
    eta_s: float | None
    fraction: float
    phase: str  # downloading | merging | verifying


@dataclass
class DownloadResult:
    path: Path
    format_id: str | None
    width: int | None
    height: int | None
    fps: float | None
    duration: float | None
    size: int
    vcodec: str | None = None
    acodec: str | None = None
    audio_language: str | None = None
    audio_note: str | None = None


ProgressFn = Callable[[DownloadProgress], None]

QUALITY_HEIGHT = {"1080": 1080, "1440": 1440, "2160": 2160, "best": None}


class Downloader(ABC):
    @abstractmethod
    def download(
        self,
        url: str,
        dest_dir: Path,
        basename: str,
        *,
        quality: str = "1080",
        progress: ProgressFn | None = None,
        cancel: CancelToken | None = None,
        expected_duration: float | None = None,
    ) -> DownloadResult: ...
