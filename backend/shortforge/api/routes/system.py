"""System endpoints: health, hardware, live stats, dashboard, SSE events, logs, storage, dependencies."""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from shortforge import __version__
from shortforge.api.serializers import short_dict
from shortforge.core import secrets
from shortforge.core.gpu import models as model_manager
from shortforge.core.hardware import live_stats
from shortforge.core.logging import tail_log
from shortforge.database.models import (
    CandidateClip,
    Job,
    Notification,
    Short,
    Source,
    Upload,
    Video,
)
from shortforge.database.session import get_session
from shortforge.engines.media import ffmpeg as ff
from shortforge.workers.context import get_context
from shortforge.workers.queue import job_to_dict

router = APIRouter()


@router.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "version": __version__, "time": datetime.now(UTC).isoformat()}


@router.get("/system/hardware")
def hardware(refresh: bool = False) -> dict[str, Any]:
    return get_context().hardware(refresh=refresh).to_dict()


@router.get("/system/stats")
def stats(s: Session = Depends(get_session)) -> dict[str, Any]:
    ctx = get_context()
    counts = dict(s.execute(select(Job.status, func.count(Job.id)).group_by(Job.status)).all())
    running = s.execute(select(Job).where(Job.status == "running").order_by(Job.started_at)).scalars().all()
    ls = live_stats()
    return {
        "live": ls.__dict__,
        "queue": {"counts": counts, "paused": ctx.queue.paused if ctx.queue else False,
                  "running": [job_to_dict(j) for j in running]},
        "models_loaded": model_manager.loaded(),
    }


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for f in path.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            continue
    return total


_storage_cache: dict[str, Any] = {"ts": 0.0, "data": None}


def storage_usage(force: bool = False) -> dict[str, Any]:
    now = datetime.now(UTC).timestamp()
    if not force and _storage_cache["data"] and now - _storage_cache["ts"] < 60:
        return _storage_cache["data"]
    paths = get_context().paths
    folders = {name: _dir_size(paths.root / name) for name in
               ("Sources", "Proxies", "Renders", "Cache", "Models", "Thumbnails", "Captions", "Temp", "Logs")}
    usage = shutil.disk_usage(paths.root)
    data = {"root": str(paths.root), "folders": folders, "total_bytes": sum(folders.values()),
            "disk_free": usage.free, "disk_total": usage.total}
    _storage_cache.update(ts=now, data=data)
    return data


@router.get("/system/storage")
def storage(force: bool = False) -> dict[str, Any]:
    return storage_usage(force)


@router.get("/system/dashboard")
def dashboard(s: Session = Depends(get_session)) -> dict[str, Any]:
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)

    def count(q) -> int:  # type: ignore[no-untyped-def]
        return int(s.execute(q).scalar() or 0)

    recent = s.execute(select(Short).order_by(Short.created_at.desc()).limit(12)).scalars().all()
    videos = {v.id: v for v in s.execute(select(Video).where(Video.id.in_([r.video_id for r in recent if r.video_id]))).scalars()}
    active = s.execute(select(Job).where(Job.status == "running").order_by(Job.started_at).limit(3)).scalars().all()
    return {
        "counts": {
            "channels_monitored": count(select(func.count(Source.id)).where(Source.enabled.is_(True),
                                                                            Source.kind.in_(("channel", "handle")))),
            "sources": count(select(func.count(Source.id))),
            "videos_discovered": count(select(func.count(Video.id))),
            "videos_downloaded": count(select(func.count(Video.id)).where(Video.download_status.in_(("done", "purged")))),
            "videos_processed": count(select(func.count(Video.id)).where(Video.processing_status == "analyzed")),
            "candidate_clips": count(select(func.count(CandidateClip.id))),
            "shorts_generated": count(select(func.count(Short.id))),
            "shorts_ready": count(select(func.count(Short.id)).where(Short.status.in_(("ready", "review")))),
            "scheduled": count(select(func.count(Upload.id)).where(Upload.status == "scheduled")),
            "uploaded_today": count(select(func.count(Upload.id)).where(Upload.status == "uploaded",
                                                                        Upload.finished_at >= today)),
            "jobs_queued": count(select(func.count(Job.id)).where(Job.status == "queued")),
            "jobs_failed_24h": count(select(func.count(Job.id)).where(
                Job.status == "failed", Job.finished_at >= datetime.now(UTC) - timedelta(hours=24))),
        },
        "active": [job_to_dict(j) for j in active],
        "recent_shorts": [short_dict(r, video=videos.get(r.video_id)) for r in recent],
        "storage": storage_usage(),
        "unread_notifications": count(select(func.count(Notification.id)).where(Notification.read.is_(False))),
    }


@router.get("/system/dependencies")
def dependencies() -> dict[str, Any]:
    ctx = get_context()
    hw = ctx.hardware()
    import yt_dlp

    from shortforge.engines.llm.providers import OllamaProvider, find_ollama_binary
    from shortforge.engines.youtube.ytdlp_backend import js_runtime_options

    settings = ctx.settings()
    ollama = OllamaProvider("", settings.llm.ollama_url)
    return {
        "ffmpeg": {"ok": bool(hw.ffmpeg_path), "path": hw.ffmpeg_path, "version": hw.ffmpeg_version,
                   "libass": ff.has_filter("ass") if hw.ffmpeg_path else False},
        "cuda": {"ok": hw.cuda_available, "devices": hw.cuda_device_count},
        "nvenc": hw.nvenc,
        "ollama": {"installed": bool(find_ollama_binary()), "running": ollama.is_available(),
                   "models": ollama.list_models()},
        "yt_dlp": {"version": yt_dlp.version.__version__},
        "js_runtime": list(js_runtime_options(settings.youtube.js_runtime).keys()),
        "keyring": secrets.backend_name(),
        "youtube_api_key": secrets.has_secret(secrets.YOUTUBE_API_KEY),
    }


@router.post("/system/shutdown")
async def shutdown() -> dict[str, Any]:
    """Graceful stop (used by the desktop shell before upgrading the engine)."""
    import os
    import signal

    loop = asyncio.get_running_loop()
    loop.call_later(0.3, lambda: os.kill(os.getpid(), signal.SIGINT if os.name != "nt" else signal.SIGTERM))
    return {"ok": True}


@router.get("/system/tools")
def tools_status() -> list[dict[str, Any]]:
    from shortforge.core.tools import TOOLS, installed_tool
    from shortforge.engines.youtube.ytdlp_backend import js_runtime_options

    found = ff.find_ffmpeg()
    js = js_runtime_options(get_context().settings().youtube.js_runtime)
    status = {"ffmpeg": bool(found), "deno": bool(js)}
    return [{"name": t.name, "label": t.label, "size_mb": t.approx_mb, "available": status[t.name],
             "managed": installed_tool(t.name) is not None,
             "detail": (found[0] if t.name == "ffmpeg" and found else ", ".join(js.keys()) if t.name == "deno" else None)}
            for t in TOOLS.values()]


@router.post("/system/tools/{name}/install")
def install_tool(name: str) -> dict[str, Any]:
    from fastapi import HTTPException

    from shortforge.core.tools import TOOLS

    if name not in TOOLS:
        raise HTTPException(404, "Unknown tool")
    return {"job_id": get_context().queue.enqueue("install_tool", {"tool": name}, dedupe_key=f"tool:{name}")}


@router.post("/system/cleanup")
def cleanup_now() -> dict[str, Any]:
    return {"job_id": get_context().queue.enqueue("storage_cleanup", priority=80, dedupe_key="cleanup")}


@router.get("/system/logs")
def logs(lines: int = 300, level: str | None = None) -> list[dict]:
    return tail_log(get_context().paths.logs, min(lines, 2000), level)


@router.get("/events")
async def events(request: Request) -> EventSourceResponse:
    bus = get_context().bus
    q = bus.subscribe()

    async def gen():  # type: ignore[no-untyped-def]
        try:
            yield {"event": "hello", "data": json.dumps({"ts": datetime.now(UTC).isoformat()})}
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield {"event": "message", "data": json.dumps(ev, default=str)}
                except TimeoutError:
                    yield {"event": "ping", "data": "{}"}
        finally:
            bus.unsubscribe(q)

    return EventSourceResponse(gen())
