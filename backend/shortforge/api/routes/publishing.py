"""Schedule, uploads, YouTube account connection, analytics and learning."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shortforge.api.serializers import short_dict, upload_dict
from shortforge.core import secrets
from shortforge.core.settings_store import get_value, set_value
from shortforge.database.models import AnalyticsSnapshot, Job, Short, Upload, Video
from shortforge.database.session import get_session
from shortforge.engines.learning.optimizer import describe_weights
from shortforge.engines.publishing import youtube_auth as yauth
from shortforge.engines.publishing.scheduler import next_slot
from shortforge.engines.ranking.weights import DEFAULT_WEIGHTS
from shortforge.workers.context import get_context
from shortforge.workers.helpers import ranking_weights

router = APIRouter()


# ---------------------------------------------------------------------------- schedule / uploads
@router.get("/uploads")
def list_uploads(status: str | None = None, limit: int = 200, s: Session = Depends(get_session)) -> list[dict[str, Any]]:
    q = select(Upload)
    if status:
        q = q.where(Upload.status.in_(status.split(",")))
    rows = s.execute(q.order_by(Upload.scheduled_at.desc().nulls_last(), Upload.id.desc()).limit(min(limit, 500))).scalars().all()
    shorts = {sh.id: sh for sh in s.execute(select(Short).where(Short.id.in_({u.short_id for u in rows}))).scalars()}
    return [{**upload_dict(u), "short": short_dict(shorts[u.short_id]) if u.short_id in shorts else None} for u in rows]


class UploadPatch(BaseModel):
    scheduled_at: datetime | None = None
    visibility: str | None = None
    title: str | None = None


@router.patch("/uploads/{upload_id}")
def patch_upload(upload_id: int, body: UploadPatch, s: Session = Depends(get_session)) -> dict[str, Any]:
    up = s.get(Upload, upload_id)
    if up is None:
        raise HTTPException(404, "Upload not found")
    if up.status != "scheduled":
        raise HTTPException(409, "Only scheduled uploads can be changed")
    if body.scheduled_at:
        when = body.scheduled_at if body.scheduled_at.tzinfo else body.scheduled_at.replace(tzinfo=UTC)
        up.scheduled_at = when
        if up.publish_at is not None:
            up.publish_at = when
        s.execute(Job.__table__.update().where(Job.dedupe_key == f"upload:{upload_id}", Job.status == "queued")
                  .values(not_before=None if up.publish_at is not None else when))
    if body.visibility:
        up.visibility = body.visibility
    if body.title:
        up.title = body.title
    return upload_dict(up)


@router.delete("/uploads/{upload_id}")
def cancel_upload(upload_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    up = s.get(Upload, upload_id)
    if up is None:
        raise HTTPException(404, "Upload not found")
    if up.status not in ("scheduled", "failed"):
        raise HTTPException(409, "Upload already in progress or finished")
    job = s.execute(select(Job).where(Job.dedupe_key == f"upload:{upload_id}", Job.status.in_(("queued", "paused")))).scalar()
    if job:
        job.status = "cancelled"
    sh = s.get(Short, up.short_id)
    if sh and sh.status in ("scheduled", "failed"):
        sh.status = "ready"
    s.delete(up)
    return {"ok": True}


@router.get("/schedule/preview")
def schedule_preview(count: int = 6, s: Session = Depends(get_session)) -> dict[str, Any]:
    ap = get_context().settings().autopilot
    taken = [u.publish_at or u.scheduled_at for u in s.execute(select(Upload).where(
        Upload.status.in_(("scheduled", "uploading", "uploaded")),
        Upload.scheduled_at >= datetime.now(UTC) - timedelta(days=1))).scalars() if (u.publish_at or u.scheduled_at)]
    slots = []
    now = datetime.now(UTC)
    for _ in range(min(count, 20)):
        try:
            t = next_slot(now, strategy=ap.schedule_strategy, slots=ap.schedule_slots, taken=taken,
                          max_per_day=ap.max_uploads_per_day, min_gap_min=ap.min_upload_gap_min)
        except ValueError:
            break
        slots.append(t.isoformat())
        taken.append(t)
    return {"next_slots": slots, "strategy": ap.schedule_strategy}


# ---------------------------------------------------------------------------- account
@router.get("/accounts/youtube")
def youtube_account() -> dict[str, Any]:
    state = yauth.auth_state()
    info: dict[str, Any] = {"client_configured": yauth.has_client_config(),
                            "connected": secrets.has_secret(secrets.YOUTUBE_OAUTH_TOKEN),
                            "auth_status": state.status, "auth_message": state.message, "channel": None}
    if info["connected"]:
        try:
            creds = yauth.load_credentials()
            info["channel"] = yauth.connected_channel(creds) if creds else None
        except Exception as exc:
            info["error"] = yauth.explain_api_error(exc) or getattr(exc, "message", str(exc))
    return info


@router.post("/accounts/youtube/client")
def youtube_client_config(body: dict[str, Any]) -> dict[str, Any]:
    try:
        yauth.save_client_config(body.get("json") or body)
    except (yauth.AuthError, ValueError) as exc:
        raise HTTPException(422, getattr(exc, "message", str(exc))) from exc
    return {"ok": True}


@router.post("/accounts/youtube/connect")
def youtube_connect() -> dict[str, Any]:
    try:
        state = yauth.start_oauth_flow()
    except yauth.AuthError as exc:
        raise HTTPException(400, exc.message) from exc
    return {"status": state.status, "message": state.message}


@router.post("/accounts/youtube/disconnect")
def youtube_disconnect() -> dict[str, Any]:
    yauth.disconnect()
    return {"ok": True}


# ---------------------------------------------------------------------------- analytics
@router.get("/analytics/summary")
def analytics_summary(s: Session = Depends(get_session)) -> dict[str, Any]:
    latest = (select(AnalyticsSnapshot.short_id, func.max(AnalyticsSnapshot.id).label("sid"))
              .group_by(AnalyticsSnapshot.short_id).subquery())
    snaps = s.execute(select(AnalyticsSnapshot).join(latest, latest.c.sid == AnalyticsSnapshot.id)).scalars().all()
    shorts = {sh.id: sh for sh in s.execute(select(Short).where(Short.id.in_([x.short_id for x in snaps]))).scalars()}
    videos = {v.id: v for v in s.execute(select(Video).where(Video.id.in_({sh.video_id for sh in shorts.values()}))).scalars()}
    uploads = {u.short_id: u for u in s.execute(select(Upload).where(Upload.short_id.in_(list(shorts)))).scalars()}
    rows = []
    for snap in snaps:
        sh = shorts.get(snap.short_id)
        if sh is None:
            continue
        up = uploads.get(sh.id)
        rows.append({"short": short_dict(sh, video=videos.get(sh.video_id)), "views": snap.views, "likes": snap.likes,
                     "comments": snap.comments, "average_view_duration": snap.average_view_duration,
                     "average_view_percentage": snap.average_view_percentage,
                     "fetched_at": snap.fetched_at.isoformat(),
                     "published_at": (up.publish_at or up.finished_at).isoformat() if up and (up.publish_at or up.finished_at) else None,
                     "youtube_video_id": snap.youtube_video_id})
    rows.sort(key=lambda r: -(r["views"] or 0))
    history = s.execute(select(func.date(AnalyticsSnapshot.fetched_at), func.sum(AnalyticsSnapshot.views))
                        .group_by(func.date(AnalyticsSnapshot.fetched_at)).order_by(func.date(AnalyticsSnapshot.fetched_at))).all()
    return {
        "totals": {"shorts": len(rows), "views": sum(r["views"] or 0 for r in rows),
                   "likes": sum(r["likes"] or 0 for r in rows), "comments": sum(r["comments"] or 0 for r in rows)},
        "rows": rows,
        "history": [{"date": str(d), "views": int(v or 0)} for d, v in history],
        "connected": secrets.has_secret(secrets.YOUTUBE_OAUTH_TOKEN),
    }


@router.post("/analytics/refresh")
def analytics_refresh() -> dict[str, Any]:
    return {"job_id": get_context().queue.enqueue("refresh_analytics", priority=85, dedupe_key="analytics")}


@router.get("/analytics/learning")
def learning(s: Session = Depends(get_session)) -> dict[str, Any]:
    state = get_value(s, "learning_state", {}) or {}
    return {"weights": describe_weights(ranking_weights(s)), "state": state,
            "settings": get_context().settings().learning.model_dump()}


@router.post("/analytics/learning/run")
def learning_run() -> dict[str, Any]:
    return {"job_id": get_context().queue.enqueue("learning_update", priority=85, dedupe_key="learning")}


@router.post("/analytics/learning/reset")
def learning_reset(s: Session = Depends(get_session)) -> dict[str, Any]:
    set_value(s, "ranking_weights", dict(DEFAULT_WEIGHTS))
    set_value(s, "learning_state", {"reset_at": datetime.now(UTC).isoformat()})
    return {"ok": True}


class WeightsIn(BaseModel):
    weights: dict[str, float]


@router.put("/analytics/weights")
def set_weights(body: WeightsIn, s: Session = Depends(get_session)) -> dict[str, Any]:
    clean = {k: max(0.0, min(5.0, float(v))) for k, v in body.weights.items() if k in DEFAULT_WEIGHTS}
    set_value(s, "ranking_weights", {**ranking_weights(s), **clean})
    return {"weights": describe_weights(ranking_weights(s))}
