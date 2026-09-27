"""FFmpeg / ffprobe discovery, probing and execution with progress + cancellation."""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from shortforge.core.errors import DependencyMissing, FFmpegError, JobCancelled
from shortforge.core.logging import get_logger

log = get_logger("ffmpeg")

_override: str | None = None

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def set_ffmpeg_override(path: str | None) -> None:
    global _override
    _override = path
    reset_caches()


def reset_caches() -> None:
    """Forget cached discovery results (after installing or changing FFmpeg)."""
    for fn in (find_ffmpeg, ffmpeg_version, available_encoders, encoder_works):
        fn.cache_clear()


def _candidate_dirs() -> list[Path]:
    dirs: list[Path] = []
    try:  # managed copy installed by ShortForge itself (Settings -> Tools)
        from shortforge.core.tools import tool_bin_dir

        dirs.append(tool_bin_dir("ffmpeg"))
    except Exception:
        pass
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(Path(local) / "Microsoft" / "WinGet" / "Links")
        for match in glob.glob(str(Path(local) / "Microsoft" / "WinGet" / "Packages" / "*FFmpeg*" / "*" / "bin")):
            dirs.append(Path(match))
    for base in (r"C:\ffmpeg\bin", r"C:\Program Files\ffmpeg\bin", r"C:\ProgramData\chocolatey\bin"):
        dirs.append(Path(base))
    home = Path.home()
    dirs.append(home / "scoop" / "shims")
    return dirs


@lru_cache(maxsize=1)
def find_ffmpeg() -> tuple[str, str] | None:
    """Return (ffmpeg, ffprobe) executable paths, or None if FFmpeg is not installed."""
    exe = ".exe" if os.name == "nt" else ""
    if _override:
        p = Path(_override)
        folder = p if p.is_dir() else p.parent
        ff, fp = folder / f"ffmpeg{exe}", folder / f"ffprobe{exe}"
        if ff.exists() and fp.exists():
            return str(ff), str(fp)
    ff, fp = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if ff and fp:
        return ff, fp
    for folder in _candidate_dirs():
        ff_p, fp_p = folder / f"ffmpeg{exe}", folder / f"ffprobe{exe}"
        if ff_p.exists() and fp_p.exists():
            return str(ff_p), str(fp_p)
    return None


def ffmpeg_bin() -> str:
    found = find_ffmpeg()
    if not found:
        raise DependencyMissing(
            "FFmpeg was not found. Install it (e.g. `winget install Gyan.FFmpeg`) or set its path in Settings."
        )
    return found[0]


def ffprobe_bin() -> str:
    found = find_ffmpeg()
    if not found:
        raise DependencyMissing("ffprobe was not found. Install FFmpeg or set its path in Settings.")
    return found[1]


@lru_cache(maxsize=1)
def ffmpeg_version() -> str | None:
    found = find_ffmpeg()
    if not found:
        return None
    try:
        out = subprocess.run(
            [found[0], "-hide_banner", "-version"], capture_output=True, text=True, timeout=15,
            creationflags=CREATE_NO_WINDOW,
        ).stdout
        first = out.splitlines()[0] if out else ""
        m = re.search(r"ffmpeg version (\S+)", first)
        return m.group(1) if m else first
    except (OSError, subprocess.SubprocessError):
        return None


@lru_cache(maxsize=1)
def available_encoders() -> frozenset[str]:
    found = find_ffmpeg()
    if not found:
        return frozenset()
    try:
        out = subprocess.run(
            [found[0], "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=15,
            creationflags=CREATE_NO_WINDOW,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    names = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] in "VAS":
            names.add(parts[1])
    return frozenset(names)


@lru_cache(maxsize=4)
def encoder_works(encoder: str) -> bool:
    """Actually try the encoder: NVENC is listed even on machines without an NVIDIA GPU."""
    if encoder not in available_encoders():
        return False
    try:
        proc = subprocess.run(
            [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
             "color=black:s=256x256:d=0.2", "-c:v", encoder, "-f", "null", "-"],
            capture_output=True, text=True, timeout=30, creationflags=CREATE_NO_WINDOW,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def has_filter(name: str) -> bool:
    found = find_ffmpeg()
    if not found:
        return False
    try:
        out = subprocess.run(
            [found[0], "-hide_banner", "-filters"], capture_output=True, text=True, timeout=15,
            creationflags=CREATE_NO_WINDOW,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return any(line.split()[1:2] == [name] for line in out.splitlines() if line.strip())


@dataclass
class MediaInfo:
    path: str
    duration: float
    width: int = 0
    height: int = 0
    fps: float = 0.0
    vcodec: str | None = None
    acodec: str | None = None
    has_video: bool = False
    has_audio: bool = False
    sample_rate: int = 0
    channels: int = 0
    bitrate: int = 0
    size: int = 0
    rotation: int = 0
    pix_fmt: str | None = None
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def display_size(self) -> tuple[int, int]:
        if self.rotation in (90, 270, -90):
            return self.height, self.width
        return self.width, self.height


def _parse_rate(rate: str | None) -> float:
    if not rate or rate in ("0/0", "N/A"):
        return 0.0
    if "/" in rate:
        num, den = rate.split("/", 1)
        try:
            d = float(den)
            return float(num) / d if d else 0.0
        except ValueError:
            return 0.0
    try:
        return float(rate)
    except ValueError:
        return 0.0


def probe(path: str | Path) -> MediaInfo:
    cmd = [ffprobe_bin(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, creationflags=CREATE_NO_WINDOW,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe failed for {Path(path).name}", stderr=proc.stderr, cmd=cmd)
    data = json.loads(proc.stdout or "{}")
    fmt = data.get("format", {})
    info = MediaInfo(path=str(path), duration=float(fmt.get("duration") or 0.0), raw=data)
    info.bitrate = int(fmt.get("bit_rate") or 0)
    info.size = int(fmt.get("size") or 0)
    for stream in data.get("streams", []):
        kind = stream.get("codec_type")
        if kind == "video" and not info.has_video and stream.get("disposition", {}).get("attached_pic") != 1:
            info.has_video = True
            info.width = int(stream.get("width") or 0)
            info.height = int(stream.get("height") or 0)
            info.vcodec = stream.get("codec_name")
            info.pix_fmt = stream.get("pix_fmt")
            info.fps = _parse_rate(stream.get("avg_frame_rate")) or _parse_rate(stream.get("r_frame_rate"))
            rot = stream.get("tags", {}).get("rotate")
            for side in stream.get("side_data_list", []) or []:
                if "rotation" in side:
                    rot = side["rotation"]
            info.rotation = int(float(rot)) if rot is not None else 0
            if not info.duration:
                info.duration = float(stream.get("duration") or 0.0)
        elif kind == "audio" and not info.has_audio:
            info.has_audio = True
            info.acodec = stream.get("codec_name")
            info.sample_rate = int(stream.get("sample_rate") or 0)
            info.channels = int(stream.get("channels") or 0)
    return info


ProgressCallback = Callable[[float, str], None]


class CancelToken:
    """Cooperative cancellation shared between the job queue and long-running subprocesses."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise JobCancelled()


_TIME_RE = re.compile(r"out_time_(?:us|ms)=(\d+)")


def run_ffmpeg(
    args: list[str],
    *,
    duration: float | None = None,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    description: str = "ffmpeg",
    stdin: int | None = None,
) -> str:
    """Run ffmpeg with machine-readable progress. Returns stderr text. Raises FFmpegError."""
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1", "-nostats", *args]
    log.debug("run %s: %s", description, " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=stdin if stdin is not None else subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW,
    )
    stderr_chunks: list[bytes] = []

    def _drain_stderr() -> None:
        assert proc.stderr is not None
        for line in iter(proc.stderr.readline, b""):
            stderr_chunks.append(line)
            if len(stderr_chunks) > 4000:
                del stderr_chunks[:2000]

    t = threading.Thread(target=_drain_stderr, daemon=True)
    t.start()
    assert proc.stdout is not None
    try:
        for raw in iter(proc.stdout.readline, b""):
            if cancel and cancel.cancelled:
                proc.kill()
                proc.wait()
                raise JobCancelled()
            line = raw.decode("utf-8", errors="replace").strip()
            m = _TIME_RE.match(line)
            if m and progress and duration:
                seconds = int(m.group(1)) / 1_000_000
                progress(max(0.0, min(1.0, seconds / duration)), description)
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
    t.join(timeout=5)
    stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace")
    if proc.returncode != 0:
        raise FFmpegError(f"{description} failed (exit code {proc.returncode})", stderr=stderr, cmd=cmd)
    if progress:
        progress(1.0, description)
    return stderr


def run_ffmpeg_capture(args: list[str], timeout: float = 600) -> str:
    """Run ffmpeg for analysis filters (e.g. loudnorm, blackdetect) and return stderr."""
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", *args]
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW)
    stderr = proc.stderr.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        raise FFmpegError("ffmpeg analysis failed", stderr=stderr, cmd=cmd)
    return stderr


def escape_filter_path(path: str | Path) -> str:
    """Escape a filesystem path for use inside an FFmpeg filter argument (e.g. ass=...)."""
    p = str(Path(path)).replace("\\", "/")
    # Filter-level escaping: ':' separates options and '\'' quotes values.
    p = p.replace(":", r"\:").replace("'", r"\'")
    return p


@dataclass
class EncoderChoice:
    name: str
    args: list[str]
    hardware: bool


def choose_video_encoder(codec: str = "h264", preference: str = "auto", profile: str = "BALANCED",
                         cq: int | None = None, bitrate_kbps: int | None = None) -> EncoderChoice:
    """Pick NVENC when it works, else a CPU encoder. Quality targets are tuned for 1080x1920 Shorts."""
    nv = {"h264": "h264_nvenc", "hevc": "hevc_nvenc", "av1": "av1_nvenc"}[codec]
    cpu = {"h264": "libx264", "hevc": "libx265", "av1": "libsvtav1"}[codec]
    use_nv = preference in ("auto", "nvenc") and encoder_works(nv)
    if preference == "nvenc" and not use_nv:
        log.warning("NVENC requested but unavailable; falling back to %s", cpu)
    if use_nv:
        preset = {"FAST": "p3", "BALANCED": "p5", "ULTRA": "p7"}[profile]
        q = cq if cq is not None else {"FAST": 25, "BALANCED": 21, "ULTRA": 18}[profile]
        args = ["-c:v", nv, "-preset", preset, "-tune", "hq", "-rc", "vbr", "-cq", str(q),
                "-spatial-aq", "1", "-temporal-aq", "1", "-rc-lookahead", "20", "-bf", "3"]
        if bitrate_kbps:
            args += ["-b:v", f"{bitrate_kbps}k", "-maxrate", f"{int(bitrate_kbps * 1.5)}k",
                     "-bufsize", f"{bitrate_kbps * 2}k"]
        else:
            args += ["-b:v", "0", "-maxrate", "30M", "-bufsize", "60M"]
        if codec == "h264":
            args += ["-profile:v", "high"]
        return EncoderChoice(nv, args, True)
    if cpu not in available_encoders():
        cpu = "libx264"
    preset = {"FAST": "veryfast", "BALANCED": "medium", "ULTRA": "slow"}[profile]
    q = cq if cq is not None else {"FAST": 23, "BALANCED": 20, "ULTRA": 17}[profile]
    if cpu == "libsvtav1":
        args = ["-c:v", cpu, "-preset", {"FAST": "10", "BALANCED": "7", "ULTRA": "5"}[profile], "-crf", str(q + 8)]
    else:
        args = ["-c:v", cpu, "-preset", preset, "-crf", str(q)]
        if cpu == "libx264":
            args += ["-profile:v", "high"]
    if bitrate_kbps:
        args += ["-maxrate", f"{bitrate_kbps}k", "-bufsize", f"{bitrate_kbps * 2}k"]
    return EncoderChoice(cpu, args, False)
