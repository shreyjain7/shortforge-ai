"""Efficient frame sampling via a single FFmpeg process per time range."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import numpy as np

from shortforge.engines.media import ffmpeg as ff


def sample_frames(video: Path, start: float, end: float, fps: float, width: int = 640,
                  src_w: int | None = None, src_h: int | None = None) -> Iterator[tuple[float, np.ndarray]]:
    """Yield (timestamp, BGR frame) at ``fps`` between start and end, scaled to ``width``."""
    if src_w is None or src_h is None:
        info = ff.probe(video)
        src_w, src_h = info.display_size
    height = max(2, round(width * src_h / max(1, src_w) / 2) * 2)
    duration = max(0.05, end - start)
    cmd = [
        ff.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin",
        "-ss", f"{max(0.0, start):.3f}", "-i", str(video), "-t", f"{duration:.3f}",
        "-vf", f"fps={fps},scale={width}:{height}:flags=area", "-an", "-sn",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1",
    ]
    frame_bytes = width * height * 3
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            creationflags=ff.CREATE_NO_WINDOW, bufsize=frame_bytes * 2)
    assert proc.stdout is not None
    idx = 0
    try:
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            frame = np.frombuffer(buf, dtype=np.uint8).reshape(height, width, 3)
            yield start + idx / fps, frame
            idx += 1
    finally:
        proc.kill()
        proc.wait()
