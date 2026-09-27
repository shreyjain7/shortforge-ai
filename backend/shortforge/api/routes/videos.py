"""Videos: listing, detail (semantic timeline, scenes, candidates), transcript, processing actions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from shortforge.api.serializers import candidate_dict, video_dict
from shortforge.database.models import (
    CandidateClip,
    Job,
    Scene,
    Short,
    Source,
    TimelineSegment,
    Transcript,
    Video,
)
from shortforge.database.session import get_session
from shortforge.engines.youtube.urls import SourceParseError, parse_source
from shortforge.workers.context import get_context
from shortforge.workers.helpers import latest_transcript, load_words
from shortforge.workers.queue import ACTIVE, job_to_dict

router = APIRouter()


@router.get("/videos")
def list_videos(source_id: int | None = None, status: str | None = None, q: str | None = None, limit: int = 60,
                offset: int = 0, s: Session = Depends(get_session)) -> dict[str, Any]:
    query = select(Video)
    if source_id:
        query = query.where(Video.source_id == source_id)
    if status == "processing":
        query = query.where(Video.processing_status.in_(("queued", "processing")))
    elif status:
        query = query.where(Video.processing_status == status)
    if q:
        like = f"%{q}%"
        query = query.where(or_(Video.title.ilike(like), Video.channel_name.ilike(like)))
    total = s.execute(select(func.count()).select_from(query.subquery())).scalar()
    rows = s.execute(query.order_by(Video.published_at.desc().nulls_last(), Video.id.desc())
                     .limit(min(limit, 200)).offset(offset)).scalars().all()
    ids = [v.id for v in rows]
    cand_counts = dict(s.execute(select(CandidateClip.video_id, func.count(CandidateClip.id))
                                 .where(CandidateClip.video_id.in_(ids)).group_by(CandidateClip.video_id)).all())
    short_counts = dict(s.execute(select(Short.video_id, func.count(Short.id)).where(Short.video_id.in_(ids))
                                  .group_by(Short.video_id)).all())
    return {"total": total, "items": [video_dict(v, {"candidates": cand_counts.get(v.id, 0),
                                                     "shorts": short_counts.get(v.id, 0)}) for v in rows]}


@router.get("/videos/{video_id}")
def get_video(video_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    v = s.get(Video, video_id)
    if v is None:
        raise HTTPException(404, "Video not found")
    segs = s.execute(select(TimelineSegment).where(TimelineSegment.video_id == video_id)
                     .order_by(TimelineSegment.start)).scalars().all()
    scenes = s.execute(select(Scene.start).where(Scene.video_id == video_id).order_by(Scene.start)).scalars().all()
    cands = s.execute(select(CandidateClip).where(CandidateClip.video_id == video_id)
                      .order_by(CandidateClip.score.desc())).scalars().all()
    jobs = s.execute(select(Job).where(Job.video_id == video_id).order_by(Job.id.desc()).limit(30)).scalars().all()
    t = latest_transcript(s, video_id)
    shorts = s.execute(select(Short.id, Short.status, Short.title).where(Short.video_id == video_id)).all()
    return video_dict(v, {
        "timeline": [{"start": x.start, "end": x.end, "label": x.label, "summary": x.summary, "interest": x.interest,
                      "energy": x.energy, "source": x.source} for x in segs],
        "scene_cuts": [float(x) for x in scenes[1:]],
        "candidates": [candidate_dict(c) for c in cands],
        "jobs": [job_to_dict(j) for j in jobs],
        "transcript": {"model": t.model, "language": t.language, "device": t.device, "words": None,
                       "elapsed_s": t.elapsed_s} if t else None,
        "shorts": [{"id": i, "status": st, "title": ti} for i, st, ti in shorts],
        "source": {"id": v.source_id, "title": s.get(Source, v.source_id).title} if v.source_id and s.get(Source, v.source_id) else None,
    })


@router.get("/videos/{video_id}/transcript")
def transcript(video_id: int, words: bool = False, s: Session = Depends(get_session)) -> dict[str, Any]:
    t = latest_transcript(s, video_id)
    if t is None:
        raise HTTPException(404, "No transcript yet")
    out: dict[str, Any] = {"model": t.model, "language": t.language, "language_probability": t.language_probability,
                           "device": t.device, "compute_type": t.compute_type, "duration": t.duration,
                           "elapsed_s": t.elapsed_s, "sentences": t.sentences, "text": t.text}
    if words:
        out["words"] = [{"i": w.idx, "w": w.text, "s": w.start, "e": w.end, "p": w.prob} for w in load_words(s, t)]
    return out


def _active_job(s: Session, video_id: int) -> bool:
    return bool(s.execute(select(Job.id).where(Job.video_id == video_id, Job.status.in_(ACTIVE)).limit(1)).scalar())


@router.post("/videos/{video_id}/process")
def process(video_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    """Run (or resume) the full pipeline from wherever this video currently is."""
    v = s.get(Video, video_id)
    if v is None:
        raise HTTPException(404, "Video not found")
    q = get_context().queue
    v.error = None
    if v.download_status not in ("done",) or not v.local_path or not Path(v.local_path).exists():
        if not v.youtube_id:
            raise HTTPException(400, "Local file is missing.")
        v.download_status, v.processing_status = "queued", "queued"
        s.commit()
        return {"job_id": q.enqueue("download_video", video_id=video_id, source_id=v.source_id, priority=70,
                                    dedupe_key=f"download:{video_id}")}
    v.processing_status = "queued"
    s.commit()
    if not v.proxy_path or not Path(v.proxy_path).exists():
        return {"job_id": q.enqueue("prepare_media", video_id=video_id, priority=70, dedupe_key=f"prepare:{video_id}")}
    jobs = []
    if v.transcript_status != "done":
        jobs.append(q.enqueue("transcribe", video_id=video_id, priority=70, dedupe_key=f"transcribe:{video_id}"))
    if v.scenes_status != "done":
        jobs.append(q.enqueue("detect_scenes", video_id=video_id, priority=70, dedupe_key=f"scenes:{video_id}"))
    if not jobs:
        jobs.append(q.enqueue("analyze_video", video_id=video_id, priority=70, dedupe_key=f"analyze:{video_id}"))
    return {"job_ids": jobs}


@router.post("/videos/{video_id}/reanalyze")
def reanalyze(video_id: int, retranscribe: bool = False, s: Session = Depends(get_session)) -> dict[str, Any]:
    v = s.get(Video, video_id)
    if v is None:
        raise HTTPException(404, "Video not found")
    q = get_context().queue
    if retranscribe:
        s.execute(delete(Transcript).where(Transcript.video_id == video_id))
        v.transcript_status = "none"
        s.commit()
        return {"job_id": q.enqueue("transcribe", {"force": True}, video_id=video_id, priority=70,
                                    dedupe_key=f"transcribe:{video_id}")}
    return {"job_id": q.enqueue("analyze_video", video_id=video_id, priority=70, dedupe_key=f"analyze:{video_id}")}


@router.post("/videos/{video_id}/find-more")
def find_more(video_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    if s.get(Video, video_id) is None:
        raise HTTPException(404, "Video not found")
    return {"job_id": get_context().queue.enqueue("find_clips", {"append": True, "no_generate": True},
                                                  video_id=video_id, priority=70, dedupe_key=f"find:{video_id}")}


class GenerateIn(BaseModel):
    count: int = 3
    min_score: float | None = None


@router.post("/videos/{video_id}/generate")
def generate(video_id: int, body: GenerateIn, s: Session = Depends(get_session)) -> dict[str, Any]:
    from shortforge.workers.helpers import effective, source_for
    from shortforge.workers.stages import PRIORITY, create_short

    v = s.get(Video, video_id)
    if v is None:
        raise HTTPException(404, "Video not found")
    eff = effective(get_context().settings(), source_for(s, v))
    q = select(CandidateClip).where(CandidateClip.video_id == video_id, CandidateClip.status == "candidate",
                                    CandidateClip.duplicate_of.is_(None)).order_by(CandidateClip.score.desc())
    cands = [c for c in s.execute(q).scalars() if body.min_score is None or c.score >= body.min_score][: body.count]
    ids = [create_short(s, c, eff["caption_preset"], eff["render_profile"], eff["reframe_mode"]) for c in cands]
    s.commit()
    for sid in ids:
        get_context().queue.enqueue("render_short", short_id=sid, video_id=video_id, priority=PRIORITY["render"] + 5,
                                    dedupe_key=f"render:{sid}")
    return {"short_ids": ids}


@router.delete("/videos/{video_id}")
def delete_video(video_id: int, delete_files: bool = False, s: Session = Depends(get_session)) -> dict[str, Any]:
    v = s.get(Video, video_id)
    if v is None:
        raise HTTPException(404, "Video not found")
    if _active_job(s, video_id):
        raise HTTPException(409, "This video is being processed; cancel its jobs first.")
    if delete_files:
        for p in (v.proxy_path, v.audio_path, v.thumbnail_path, v.local_path if v.youtube_id else None):
            if p and Path(p).exists():
                Path(p).unlink(missing_ok=True)
    s.delete(v)
    return {"ok": True}


class ImportIn(BaseModel):
    path: str


@router.post("/videos/import")
def import_local(body: ImportIn, s: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        ref = parse_source(body.path)
    except SourceParseError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not Path(ref.value).exists():
        raise HTTPException(404, "File not found")
    existing = s.execute(select(Video).where(Video.local_path == ref.value)).scalar()
    if existing:
        return {"video_id": existing.id, "existing": True}
    v = Video(title=Path(ref.value).stem, local_path=ref.value, source_url=ref.value, download_status="done",
              processing_status="queued")
    s.add(v)
    s.flush()
    vid = v.id
    s.commit()
    get_context().queue.enqueue("import_local", video_id=vid, priority=70, dedupe_key=f"import:{vid}")
    return {"video_id": vid}
