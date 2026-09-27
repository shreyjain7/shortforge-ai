"""Self-contained installs of external tools (FFmpeg, Deno) for machines without them.

Downloaded into ``<data>/Tools`` so a fresh install needs no admin rights, winget or Node.js.
"""

from __future__ import annotations

import os
import shutil
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from shortforge.core.errors import RetryableError
from shortforge.core.logging import get_logger
from shortforge.core.paths import get_paths

log = get_logger("tools")


@dataclass(frozen=True)
class ToolSpec:
    name: str
    label: str
    url: str
    approx_mb: int
    executables: tuple[str, ...]


TOOLS: dict[str, ToolSpec] = {
    "ffmpeg": ToolSpec(
        "ffmpeg", "FFmpeg (with NVENC + libass)",
        "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
        150, ("ffmpeg.exe", "ffprobe.exe"),
    ),
    "deno": ToolSpec(
        "deno", "Deno (JavaScript runtime used to read YouTube)",
        "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip",
        45, ("deno.exe",),
    ),
}


def tools_dir() -> Path:
    path = get_paths().root / "Tools"
    path.mkdir(parents=True, exist_ok=True)
    return path


def tool_bin_dir(name: str) -> Path:
    return tools_dir() / name


def installed_tool(name: str) -> Path | None:
    """Folder containing the tool's executables if a managed copy exists."""
    folder = tool_bin_dir(name)
    spec = TOOLS[name]
    if all((folder / exe).exists() for exe in spec.executables):
        return folder
    return None


def install_tool(name: str, progress: Callable[[float, str], None] | None = None,
                 cancelled: Callable[[], bool] | None = None) -> Path:
    if os.name != "nt":
        raise RetryableError("Automatic tool installs are only available on Windows; install it with your package manager.")
    spec = TOOLS[name]
    target = tool_bin_dir(name)
    tmp = tools_dir() / f"{name}.download.zip"
    try:
        with httpx.stream("GET", spec.url, follow_redirects=True, timeout=httpx.Timeout(30, read=120)) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length") or 0) or spec.approx_mb * 1024 * 1024
            done = 0
            with tmp.open("wb") as fh:
                for chunk in resp.iter_bytes(256 * 1024):
                    if cancelled and cancelled():
                        raise RetryableError("Download cancelled")
                    fh.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(min(0.95, done / total * 0.95),
                                 f"Downloading {spec.label}: {done / 1e6:.0f} / {total / 1e6:.0f} MB")
    except httpx.HTTPError as exc:
        tmp.unlink(missing_ok=True)
        raise RetryableError(f"Could not download {spec.label}.", detail=str(exc)) from exc
    if progress:
        progress(0.96, f"Extracting {spec.label}")
    staging = tools_dir() / f"{name}.staging"
    shutil.rmtree(staging, ignore_errors=True)
    with zipfile.ZipFile(tmp) as zf:
        zf.extractall(staging)
    tmp.unlink(missing_ok=True)
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    for exe in spec.executables:
        found = next(staging.rglob(exe), None)
        if found is None:
            raise RetryableError(f"{exe} was not found in the downloaded archive.")
        shutil.move(str(found), target / exe)
    shutil.rmtree(staging, ignore_errors=True)
    if progress:
        progress(1.0, f"{spec.label} installed")
    log.info("installed %s into %s", name, target)
    return target
