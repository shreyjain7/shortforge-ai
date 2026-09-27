"""Source manager: resolve, add, configure, scan and remove sources (any public channel)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shortforge.api.serializers import source_dict
from shortforge.core.errors import ShortForgeError
from shortforge.database.models import Short, Source, Video
from shortforge.database.session import get_session
from shortforge.engines.youtube.urls import SourceKind, SourceParseError, parse_source
from shortforge.workers.context import get_context

router = APIRouter()


class ResolveIn(BaseModel):
    input: str


class SourceIn(BaseModel):
    input: str
    enabled: bool = True
    auto_scan: bool = True
    priority: int = 50
    scan_interval_min: int | None = None
    max_videos_per_scan: int = 3
    min_duration_s: float | None = 180
    max_duration_s: float | None = 4 * 3600
    max_video_age_days: int | None = 30
    max_shorts_per_video: int | None = None
    max_shorts_per_day: int | None = None
    min_candidate_score: float | None = None
    short_min_s: float | None = None
    short_max_s: float | None = None
    preferred_style: str | None = None
    reframe_mode: str | None = None
    upload_destination: str | None = None
    schedule_strategy: str | None = None
    auto_download: bool = True
    process_existing: bool = True
    scan_now: bool = True


class SourcePatch(BaseModel):
    enabled: bool | None = None
    auto_scan: bool | None = None
    priority: int | None = None
    scan_interval_min: int | None = None
    max_videos_per_scan: int | None = None
    min_duration_s: float | None = None
    max_duration_s: float | None = None
    max_video_age_days: int | None = None
    max_shorts_per_video: int | None = None
    max_shorts_per_day: int | None = None
    min_candidate_score: float | None = None
    short_min_s: float | None = None
    short_max_s: float | None = None
    preferred_style: str | None = None
    reframe_mode: str | None = None
    upload_destination: str | None = None
    schedule_strategy: str | None = None
    auto_download: bool | None = None
    process_existing: bool | None = None
    clear: list[str] = Field(default_factory=list)
    """Field names to reset to null (use the global setting)."""


def _stats(s: Session, source_ids: list[int]) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {sid: {"videos": 0, "processed": 0, "shorts": 0, "processing": 0} for sid in source_ids}
    for sid, total in s.execute(select(Video.source_id, func.count(Video.id)).where(Video.source_id.in_(source_ids))
                                .group_by(Video.source_id)).all():
        out[sid]["videos"] = total
    for sid, total in s.execute(select(Video.source_id, func.count(Video.id)).where(
            Video.source_id.in_(source_ids), Video.processing_status == "analyzed").group_by(Video.source_id)).all():
        out[sid]["processed"] = total
    for sid, total in s.execute(select(Video.source_id, func.count(Video.id)).where(
            Video.source_id.in_(source_ids), Video.processing_status.in_(("queued", "processing"))).group_by(Video.source_id)).all():
        out[sid]["processing"] = total
    for sid, total in s.execute(select(Video.source_id, func.count(Short.id)).join(Video, Video.id == Short.video_id)
                                .where(Video.source_id.in_(source_ids)).group_by(Video.source_id)).all():
        out[sid]["shorts"] = total
    return out


@router.post("/sources/resolve")
def resolve(body: ResolveIn) -> dict[str, Any]:
    """Preview a source before adding it (identifies the input type automatically)."""
    try:
        ref = parse_source(body.input)
    except SourceParseError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        resolved = get_context().source_provider().resolve_source(body.input)
    except ShortForgeError as exc:
        raise HTTPException(400, exc.message) from exc
    return {
        "kind": ref.kind.value, "value": ref.value, "url": ref.url, "title": resolved.title,
        "thumbnail_url": resolved.thumbnail_url, "video_count": resolved.video_count,
        "channel": resolved.channel.to_dict() if resolved.channel else None,
        "recent_videos": [v.to_dict() for v in resolved.recent_videos[:12]],
    }


@router.get("/sources")
def list_sources(s: Session = Depends(get_session)) -> list[dict[str, Any]]:
    rows = s.execute(select(Source).order_by(Source.priority.desc(), Source.id)).scalars().all()
    stats = _stats(s, [r.id for r in rows])
    return [source_dict(r, stats.get(r.id)) for r in rows]


@router.post("/sources")
def add_source(body: SourceIn, s: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        ref = parse_source(body.input)
    except SourceParseError as exc:
        raise HTTPException(422, str(exc)) from exc
    existing = s.execute(select(Source).where(Source.url == ref.url)).scalar()
    if existing:
        raise HTTPException(409, f"This source is already added (#{existing.id}).")
    data = body.model_dump(exclude={"input", "scan_now"})
    src = Source(kind=ref.kind.value, input=body.input.strip(), url=ref.url, title=ref.value,
                 playlist_id=ref.value if ref.kind == SourceKind.PLAYLIST else None, **data)
    if ref.kind == SourceKind.VIDEO:
        src.min_duration_s = None  # a directly-pasted video is always processed
        src.max_video_age_days = None
        src.auto_scan = False
    s.add(src)
    s.flush()
    sid = src.id
    s.commit()
    if body.scan_now:
        get_context().queue.enqueue("scan_source", source_id=sid, priority=75, dedupe_key=f"scan:{sid}")
    s.refresh(src)
    return source_dict(src, _stats(s, [sid]).get(sid))


@router.get("/sources/{source_id}")
def get_source(source_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    src = s.get(Source, source_id)
    if src is None:
        raise HTTPException(404, "Source not found")
    return source_dict(src, _stats(s, [source_id]).get(source_id))


@router.patch("/sources/{source_id}")
def patch_source(source_id: int, body: SourcePatch, s: Session = Depends(get_session)) -> dict[str, Any]:
    src = s.get(Source, source_id)
    if src is None:
        raise HTTPException(404, "Source not found")
    for key, value in body.model_dump(exclude={"clear"}, exclude_none=True).items():
        setattr(src, key, value)
    for key in body.clear:
        if hasattr(src, key) and key not in ("id", "kind", "input", "url"):
            setattr(src, key, None)
    s.flush()
    get_context().bus.publish("source.updated", {"source_id": source_id})
    return source_dict(src, _stats(s, [source_id]).get(source_id))


@router.delete("/sources/{source_id}")
def delete_source(source_id: int, delete_videos: bool = False, s: Session = Depends(get_session)) -> dict[str, Any]:
    src = s.get(Source, source_id)
    if src is None:
        raise HTTPException(404, "Source not found")
    if delete_videos:
        for v in s.execute(select(Video).where(Video.source_id == source_id, Video.processing_status.in_(
                ("new", "ignored")))).scalars():
            s.delete(v)
    s.delete(src)
    return {"ok": True}


@router.post("/sources/{source_id}/scan")
def scan(source_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    if s.get(Source, source_id) is None:
        raise HTTPException(404, "Source not found")
    job = get_context().queue.enqueue("scan_source", source_id=source_id, priority=80, dedupe_key=f"scan:{source_id}")
    return {"job_id": job}


class ProcessExistingIn(BaseModel):
    limit: int = 5
    newest_first: bool = True


@router.post("/sources/{source_id}/process-existing")
def process_existing(source_id: int, body: ProcessExistingIn, s: Session = Depends(get_session)) -> dict[str, Any]:
    """Queue already-indexed but unprocessed uploads (e.g. the channel's back catalogue)."""
    q = select(Video).where(Video.source_id == source_id, Video.processing_status.in_(("new", "ignored")),
                            Video.download_status.in_(("pending", "failed")))
    q = q.order_by(Video.published_at.desc() if body.newest_first else Video.published_at.asc()).limit(body.limit)
    queued = []
    for v in s.execute(q).scalars():
        v.download_status, v.processing_status, v.error = "queued", "queued", None
        queued.append(v.id)
    s.commit()
    for vid in queued:
        get_context().queue.enqueue("download_video", video_id=vid, source_id=source_id, priority=55,
                                    dedupe_key=f"download:{vid}")
    return {"queued": queued}
