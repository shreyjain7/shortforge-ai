"""Queue: list jobs, inspect logs, and control them (pause/resume/retry/cancel/priority)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from shortforge.database.models import Job
from shortforge.database.session import get_session
from shortforge.workers.context import get_context
from shortforge.workers.queue import REGISTRY, job_to_dict

router = APIRouter()

STAGE_ORDER = ["scan_source", "download_video", "import_local", "prepare_media", "transcribe", "detect_scenes",
               "analyze_video", "find_clips", "render_short", "qc_short", "short_metadata", "upload_short",
               "refresh_analytics", "learning_update", "install_model", "storage_cleanup"]


@router.get("/jobs")
def list_jobs(status: str | None = None, type: str | None = None, limit: int = 100,
              s: Session = Depends(get_session)) -> dict[str, Any]:
    q = select(Job)
    if status:
        q = q.where(Job.status.in_(status.split(",")))
    if type:
        q = q.where(Job.type == type)
    rows = s.execute(q.order_by(Job.status == "running", Job.priority.desc(), Job.id.desc())
                     .limit(min(limit, 500))).scalars().all()
    rows = sorted(rows, key=lambda j: ({"running": 0, "queued": 1, "paused": 2}.get(j.status, 3), -j.priority, -j.id))
    counts = dict(s.execute(select(Job.status, func.count(Job.id)).group_by(Job.status)).all())
    by_type = {t: c for t, c in s.execute(select(Job.type, func.count(Job.id)).where(Job.status.in_(("queued", "running")))
                                          .group_by(Job.type)).all()}
    stages = [{"type": t, "label": REGISTRY[t].label, "active": by_type.get(t, 0)} for t in STAGE_ORDER if t in REGISTRY]
    return {"items": [job_to_dict(j) for j in rows], "counts": counts, "stages": stages,
            "paused": get_context().queue.paused}


@router.get("/jobs/{job_id}")
def get_job(job_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    j = s.get(Job, job_id)
    if j is None:
        raise HTTPException(404, "Job not found")
    return {**job_to_dict(j), "logs": j.logs, "result": j.result}


def _act(ok: bool) -> dict[str, Any]:
    if not ok:
        raise HTTPException(409, "Action not possible in the job's current state")
    return {"ok": True}


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: int) -> dict[str, Any]:
    return _act(get_context().queue.cancel(job_id))


@router.post("/jobs/{job_id}/retry")
def retry(job_id: int) -> dict[str, Any]:
    return _act(get_context().queue.retry(job_id))


@router.post("/jobs/{job_id}/pause")
def pause(job_id: int) -> dict[str, Any]:
    return _act(get_context().queue.set_paused(job_id, True))


@router.post("/jobs/{job_id}/resume")
def resume(job_id: int) -> dict[str, Any]:
    return _act(get_context().queue.set_paused(job_id, False))


class PriorityIn(BaseModel):
    priority: int


@router.post("/jobs/{job_id}/priority")
def priority(job_id: int, body: PriorityIn) -> dict[str, Any]:
    return _act(get_context().queue.set_priority(job_id, body.priority))


@router.post("/jobs/{job_id}/top")
def top(job_id: int) -> dict[str, Any]:
    return _act(get_context().queue.move_to_top(job_id))


@router.post("/queue/pause")
def pause_queue() -> dict[str, Any]:
    get_context().queue.pause_all(True)
    return {"paused": True}


@router.post("/queue/resume")
def resume_queue() -> dict[str, Any]:
    get_context().queue.pause_all(False)
    return {"paused": False}


@router.delete("/jobs/finished")
def clear_finished(s: Session = Depends(get_session)) -> dict[str, Any]:
    res = s.execute(delete(Job).where(Job.status.in_(("done", "cancelled"))))
    return {"deleted": res.rowcount}
