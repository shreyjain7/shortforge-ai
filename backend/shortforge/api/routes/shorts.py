"""Shorts library, detail, manual editing (timeline versions), rendering, metadata and scheduling."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shortforge.api.serializers import short_dict
from shortforge.database.models import AnalyticsSnapshot, Job, Short, Upload, Video
from shortforge.database.models import EditTimeline as EditTimelineRow
from shortforge.database.session import get_session
from shortforge.engines.captions.presets import builtin_presets
from shortforge.engines.editing.timeline import EditTimeline
from shortforge.workers.context import get_context
from shortforge.workers.queue import ACTIVE
from shortforge.workers.stages import PRIORITY, current_timeline

router = APIRouter()

STATUSES = ("draft", "rendering", "review", "ready", "scheduled", "uploading", "published", "failed")


@router.get("/shorts")
def list_shorts(status: str | None = None, source_id: int | None = None, video_id: int | None = None,
                min_score: float | None = None, max_duration: float | None = None, min_duration: float | None = None,
                since: str | None = None, sort: str = "newest", limit: int = 60, offset: int = 0,
                s: Session = Depends(get_session)) -> dict[str, Any]:
    q = select(Short)
    if status:
        q = q.where(Short.status.in_(status.split(",")))
    if video_id:
        q = q.where(Short.video_id == video_id)
    if source_id:
        q = q.join(Video, Video.id == Short.video_id).where(Video.source_id == source_id)
    if min_score is not None:
        q = q.where(Short.score >= min_score)
    if min_duration is not None:
        q = q.where(Short.duration >= min_duration)
    if max_duration is not None:
        q = q.where(Short.duration <= max_duration)
    if since:
        q = q.where(Short.created_at >= datetime.fromisoformat(since))
    total = s.execute(select(func.count()).select_from(q.subquery())).scalar()
    if sort == "performance":
        views = (select(AnalyticsSnapshot.short_id, func.max(AnalyticsSnapshot.views).label("v"))
                 .group_by(AnalyticsSnapshot.short_id).subquery())
        q = q.outerjoin(views, views.c.short_id == Short.id).order_by(views.c.v.desc().nulls_last(), Short.id.desc())
    else:
        q = q.order_by({"score": Short.score.desc(), "oldest": Short.created_at.asc(),
                        "duration": Short.duration.asc()}.get(sort, Short.created_at.desc()))
    rows = s.execute(q.limit(min(limit, 200)).offset(offset)).scalars().all()
    videos = {v.id: v for v in s.execute(select(Video).where(Video.id.in_({r.video_id for r in rows if r.video_id}))).scalars()}
    counts = dict(s.execute(select(Short.status, func.count(Short.id)).group_by(Short.status)).all())
    return {"total": total, "counts": counts, "items": [short_dict(r, video=videos.get(r.video_id)) for r in rows]}


@router.get("/shorts/{short_id}")
def get_short(short_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    sh = s.get(Short, short_id)
    if sh is None:
        raise HTTPException(404, "Short not found")
    row = current_timeline(s, short_id)
    d = short_dict(sh, detail=True, video=s.get(Video, sh.video_id) if sh.video_id else None)
    d["timeline"] = row.data if row else None
    d["timeline_version"] = row.version if row else None
    d["versions"] = [{"version": r.version, "origin": r.origin, "created_at": r.created_at.isoformat()}
                     for r in s.execute(select(EditTimelineRow).where(EditTimelineRow.short_id == short_id)
                                        .order_by(EditTimelineRow.version.desc())).scalars()]
    d["active_job"] = bool(s.execute(select(Job.id).where(Job.short_id == short_id, Job.status.in_(ACTIVE)).limit(1)).scalar())
    snap = s.execute(select(AnalyticsSnapshot).where(AnalyticsSnapshot.short_id == short_id)
                     .order_by(AnalyticsSnapshot.fetched_at.desc()).limit(1)).scalar()
    d["analytics"] = {"views": snap.views, "likes": snap.likes, "comments": snap.comments,
                      "fetched_at": snap.fetched_at.isoformat()} if snap else None
    return d


class ShortPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    hashtags: list[str] | None = None
    caption_preset: str | None = None
    reframe_mode: str | None = None
    render_profile: str | None = None
    favorite: bool | None = None
    status: str | None = None


@router.patch("/shorts/{short_id}")
def patch_short(short_id: int, body: ShortPatch, s: Session = Depends(get_session)) -> dict[str, Any]:
    sh = s.get(Short, short_id)
    if sh is None:
        raise HTTPException(404, "Short not found")
    data = body.model_dump(exclude_none=True)
    if "status" in data and data["status"] not in ("ready", "review", "draft"):
        raise HTTPException(422, "Only ready/review/draft can be set manually")
    if "caption_preset" in data:
        from shortforge.database.models import CaptionStyle

        known = set(builtin_presets()) | set(s.execute(select(CaptionStyle.name)).scalars())
        if data["caption_preset"] not in known:
            raise HTTPException(422, f"Unknown caption preset '{data['caption_preset']}'")
    for k, v in data.items():
        setattr(sh, k, v)
    if "caption_preset" in data:
        row = current_timeline(s, short_id)
        if row is not None:
            tl = EditTimeline.model_validate(row.data)
            tl.captions.preset = data["caption_preset"]
            s.add(EditTimelineRow(short_id=short_id, version=row.version + 1, data=tl.model_dump(), origin="manual"))
    return short_dict(sh, detail=True)


@router.put("/shorts/{short_id}/timeline")
def save_timeline(short_id: int, body: dict[str, Any], render: bool = True, s: Session = Depends(get_session)) -> dict[str, Any]:
    """Save a manually edited timeline as a new version (non-destructive) and optionally re-render."""
    sh = s.get(Short, short_id)
    if sh is None:
        raise HTTPException(404, "Short not found")
    try:
        tl = EditTimeline.model_validate(body)
    except Exception as exc:
        raise HTTPException(422, f"Invalid timeline: {exc}") from exc
    if not tl.ranges or any(r.end <= r.start for r in tl.ranges):
        raise HTTPException(422, "Timeline needs at least one valid source range.")
    row = current_timeline(s, short_id)
    version = (row.version + 1) if row else 1
    s.add(EditTimelineRow(short_id=short_id, version=version, data=tl.model_dump(), origin="manual"))
    sh.start, sh.end = tl.ranges[0].start, tl.ranges[-1].end
    sh.caption_preset = tl.captions.preset
    s.commit()
    job = None
    if render:
        job = get_context().queue.enqueue("render_short", short_id=short_id, video_id=sh.video_id,
                                          priority=PRIORITY["render"] + 15, dedupe_key=f"render:{short_id}")
    return {"version": version, "job_id": job}


@router.post("/shorts/{short_id}/revert/{version}")
def revert(short_id: int, version: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    old = s.execute(select(EditTimelineRow).where(EditTimelineRow.short_id == short_id,
                                                  EditTimelineRow.version == version)).scalar()
    if old is None:
        raise HTTPException(404, "Version not found")
    return save_timeline(short_id, old.data, True, s)


@router.post("/shorts/{short_id}/render")
def rerender(short_id: int, rebuild: bool = False, s: Session = Depends(get_session)) -> dict[str, Any]:
    sh = s.get(Short, short_id)
    if sh is None:
        raise HTTPException(404, "Short not found")
    sh.repair_attempts = 0
    s.commit()
    return {"job_id": get_context().queue.enqueue("render_short", {"rebuild": rebuild}, short_id=short_id,
                                                  video_id=sh.video_id, priority=PRIORITY["render"] + 15,
                                                  dedupe_key=f"render:{short_id}")}


class MetaIn(BaseModel):
    mode: str = "clean"


@router.post("/shorts/{short_id}/metadata")
def regenerate_metadata(short_id: int, body: MetaIn, s: Session = Depends(get_session)) -> dict[str, Any]:
    sh = s.get(Short, short_id)
    if sh is None or not sh.output_path:
        raise HTTPException(404, "Render the Short first")
    return {"job_id": get_context().queue.enqueue("short_metadata", {"mode": body.mode}, short_id=short_id,
                                                  video_id=sh.video_id, priority=85, dedupe_key=f"meta:{short_id}")}


class ScheduleIn(BaseModel):
    at: datetime | None = None
    visibility: str | None = None


@router.post("/shorts/{short_id}/schedule")
def schedule(short_id: int, body: ScheduleIn, s: Session = Depends(get_session)) -> dict[str, Any]:
    from shortforge.core.errors import ShortForgeError
    from shortforge.workers.stages import schedule_short

    sh = s.get(Short, short_id)
    if sh is None:
        raise HTTPException(404, "Short not found")
    if sh.qc_status == "FAIL":
        raise HTTPException(409, "This Short failed quality control; fix and re-render it first.")
    s.commit()
    try:
        upload_id = schedule_short(short_id, body.at, body.visibility)
    except (ShortForgeError, ValueError) as exc:
        raise HTTPException(400, getattr(exc, "message", str(exc))) from exc
    return {"upload_id": upload_id}


@router.delete("/shorts/{short_id}")
def delete_short(short_id: int, delete_file: bool = False, s: Session = Depends(get_session)) -> dict[str, Any]:
    sh = s.get(Short, short_id)
    if sh is None:
        raise HTTPException(404, "Short not found")
    if s.execute(select(Upload.id).where(Upload.short_id == short_id, Upload.status.in_(("uploading",)))).scalar():
        raise HTTPException(409, "This Short is uploading right now.")
    if delete_file:
        for p in (sh.output_path, sh.cover_path):
            if p and Path(p).exists():
                Path(p).unlink(missing_ok=True)
    if sh.candidate:
        sh.candidate.status = "candidate"
    s.delete(sh)
    return {"ok": True}


@router.get("/shorts/{short_id}/caption-layout")
def caption_layout(short_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    """Exact caption page layout (positions in output pixels) for the editor overlay."""
    from shortforge.workers.stages import build_captions

    row = current_timeline(s, short_id)
    if row is None:
        raise HTTPException(404, "No timeline")
    tl = EditTimeline.model_validate(row.data)
    pages = build_captions(tl, get_context().paths.temp / f"layout_{short_id}.ass")
    return {"width": tl.width, "height": tl.height, "safe_area": tl.safe_area.model_dump(),
            "pages": [{"start": p.start, "end": p.end, "words": [{"text": w.text, "start": w.start, "end": w.end,
                                                                  "x": w.x, "y": w.y, "width": w.width,
                                                                  "emphasis": w.emphasis} for w in p.words]}
                      for p in pages]}
