"""yt-dlp + FFmpeg based downloader with resume, retries, cancellation and verification."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from shortforge.core.errors import DownloadError, JobCancelled, SourceResolutionError
from shortforge.core.logging import get_logger
from shortforge.engines.downloader.base import (
    QUALITY_HEIGHT,
    Downloader,
    DownloadProgress,
    DownloadResult,
    ProgressFn,
)
from shortforge.engines.media import ffmpeg as ff
from shortforge.engines.media.ffmpeg import CancelToken
from shortforge.engines.youtube.ytdlp_backend import base_options

log = get_logger("downloader")


def format_selector(quality: str) -> str:
    """Highest useful quality, capped at the requested height.

    With an explicit cap (1080/1440/2160) we never fetch a larger stream. 'best' takes whatever is
    highest. Separate video+audio streams are merged by FFmpeg.
    """
    height = QUALITY_HEIGHT.get(quality)
    if height is None:
        return "bv*+ba/b"
    return f"bv*[height<={height}]+ba/b[height<={height}]/bv*[height<={height}]+ba/b"


def format_sort(quality: str) -> list[str]:
    height = QUALITY_HEIGHT.get(quality)
    # 'lang' must come first: YouTube serves auto-dubbed audio tracks and yt-dlp marks the
    # original-language track with the highest language_preference. Then prefer resolution,
    # fps, higher-efficiency codecs and bitrate.
    base = ["lang", "res", "fps", "hdr:12", "vcodec", "vbr", "acodec", "abr"]
    if height:
        base[1] = f"res:{height}"
    return base


class _Cancelled(Exception):
    pass


class YtDlpDownloader(Downloader):
    def __init__(self, js_runtime: str = "node", cookies_from_browser: str | None = None,
                 rate_limit_kbps: int | None = None) -> None:
        self.js_runtime = js_runtime
        self.cookies_from_browser = cookies_from_browser
        self.rate_limit_kbps = rate_limit_kbps

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
    ) -> DownloadResult:
        import yt_dlp

        dest_dir.mkdir(parents=True, exist_ok=True)
        existing = self._find_existing(dest_dir, basename)
        if existing is not None:
            try:
                return self.verify(existing, expected_duration)
            except DownloadError:
                log.warning("existing file %s failed verification; re-downloading", existing)
                existing.unlink(missing_ok=True)

        last_emit = [0.0]

        def hook(d: dict[str, Any]) -> None:
            if cancel and cancel.cancelled:
                raise _Cancelled()
            if not progress:
                return
            now = time.monotonic()
            status = d.get("status")
            if status == "downloading" and now - last_emit[0] < 0.4:
                return
            last_emit[0] = now
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes") or 0
            frac = (done / total) if total else 0.0
            progress(DownloadProgress(done, int(total) if total else None, d.get("speed"), d.get("eta"),
                                      min(frac, 1.0), "downloading" if status != "finished" else "merging"))

        def pp_hook(d: dict[str, Any]) -> None:
            if progress and d.get("status") == "started":
                progress(DownloadProgress(0, None, None, None, 1.0, "merging"))

        ff_paths = ff.find_ffmpeg()
        opts = base_options(self.js_runtime, self.cookies_from_browser)
        opts.update({
            "format": format_selector(quality),
            "format_sort": format_sort(quality),
            "outtmpl": {"default": str(dest_dir / f"{basename}.%(ext)s")},
            "merge_output_format": "mkv",
            "continuedl": True,  # resume .part files
            "noplaylist": True,
            "progress_hooks": [hook],
            "postprocessor_hooks": [pp_hook],
            "concurrent_fragment_downloads": 4,
            "writethumbnail": False,
            "overwrites": False,
        })
        if ff_paths:
            opts["ffmpeg_location"] = str(Path(ff_paths[0]).parent)
        if self.rate_limit_kbps:
            opts["ratelimit"] = self.rate_limit_kbps * 1024

        info: dict[str, Any] | None = None
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                final = Path(ydl.prepare_filename(info)).with_suffix(".mkv") if info else None
        except _Cancelled as exc:
            raise JobCancelled() from exc
        except yt_dlp.utils.DownloadError as exc:
            if cancel and cancel.cancelled:
                raise JobCancelled() from exc
            text = str(exc).lower()
            if any(k in text for k in ("private video", "video unavailable", "members-only", "removed by",
                                       "not available in your country", "copyright")):
                raise SourceResolutionError(f"Video cannot be downloaded: {str(exc)[:300]}") from exc
            raise DownloadError("Download failed; it will be retried.", detail=str(exc)) from exc

        path = final if final and final.exists() else self._find_existing(dest_dir, basename)
        if path is None:
            raise DownloadError("Download finished but the output file is missing.")
        if progress:
            progress(DownloadProgress(0, None, None, None, 1.0, "verifying"))
        result = self.verify(path, expected_duration or (info or {}).get("duration"))
        result.format_id = (info or {}).get("format_id")
        for fmt in (info or {}).get("requested_formats") or [info or {}]:
            if fmt.get("acodec") not in (None, "none"):
                result.audio_language = fmt.get("language")
                result.audio_note = fmt.get("format_note")
        log.info("downloaded %s format=%s audio=%s (%s)", basename, result.format_id, result.audio_language,
                 result.audio_note)
        return result

    @staticmethod
    def _find_existing(dest_dir: Path, basename: str) -> Path | None:
        for ext in (".mkv", ".mp4", ".webm", ".mov"):
            p = dest_dir / f"{basename}{ext}"
            if p.exists() and p.stat().st_size > 0:
                return p
        return None

    @staticmethod
    def verify(path: Path, expected_duration: float | None = None) -> DownloadResult:
        """ffprobe the file: it must contain decodable video+audio of the expected length."""
        try:
            info = ff.probe(path)
        except ff.FFmpegError as exc:
            raise DownloadError(f"Downloaded file is unreadable: {path.name}", detail=exc.detail) from exc
        if not info.has_video:
            raise DownloadError("Downloaded file has no video stream.")
        if expected_duration and info.duration and abs(info.duration - expected_duration) > max(3.0, 0.03 * expected_duration):
            raise DownloadError(
                f"Downloaded duration {info.duration:.0f}s does not match expected {expected_duration:.0f}s."
            )
        w, h = info.display_size
        return DownloadResult(
            path=path, format_id=None, width=w, height=h, fps=info.fps, duration=info.duration,
            size=path.stat().st_size, vcodec=info.vcodec, acodec=info.acodec,
        )
