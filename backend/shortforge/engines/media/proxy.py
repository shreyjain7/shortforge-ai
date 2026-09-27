"""Proxy media: a small, fast-seeking H.264 copy used for preview playback and visual analysis."""

from __future__ import annotations

from pathlib import Path

from shortforge.core.errors import FFmpegError
from shortforge.core.logging import get_logger
from shortforge.engines.media import ffmpeg as ff

log = get_logger("proxy")


def make_proxy(source: Path, out: Path, *, height: int = 540, duration: float | None = None, progress=None,
               cancel=None) -> Path:
    """Encode a 540p proxy with a keyframe every second (frame-accurate, responsive scrubbing)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".tmp.mp4")
    use_nv = ff.encoder_works("h264_nvenc")
    venc = (["-c:v", "h264_nvenc", "-preset", "p2", "-cq", "28", "-b:v", "0"] if use_nv
            else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "26"])
    vf = f"scale=-2:'min({height},ih)':flags=bicubic,format=yuv420p"
    base = ["-i", str(source), "-map", "0:v:0", "-map", "0:a:0?", "-vf", vf, *venc,
            "-g", "30", "-keyint_min", "30", "-sc_threshold", "0", "-force_key_frames", "expr:gte(t,n_forced*1)",
            "-c:a", "aac", "-b:a", "96k", "-ac", "2", "-movflags", "+faststart", str(tmp)]
    attempts = [["-hwaccel", "auto", *base], base] if use_nv else [base]
    last: FFmpegError | None = None
    for args in attempts:
        try:
            ff.run_ffmpeg(args, duration=duration, progress=progress, cancel=cancel, description="Creating proxy")
            tmp.replace(out)
            return out
        except FFmpegError as exc:
            last = exc
            log.warning("proxy attempt failed (%s); retrying without hardware decode", exc.message)
    assert last is not None
    raise last


def make_thumbnail(source: Path, out: Path, at: float, width: int = 640) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    ff.run_ffmpeg(["-ss", f"{max(0.0, at):.2f}", "-i", str(source), "-frames:v", "1",
                   "-vf", f"scale={width}:-2", "-q:v", "3", str(out)], description="Thumbnail")
    return out
