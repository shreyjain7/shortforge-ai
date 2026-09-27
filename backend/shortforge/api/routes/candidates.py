"""Candidate clips: browse, inspect scores, generate, reject, favourite, edit boundaries."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shortforge.api.serializers import candidate_dict
from shortforge.database.models import CandidateClip, Video
from shortforge.database.session import get_session
from shortforge.engines.transcription.types import words_text
from shortforge.workers.context import get_context
from shortforge.workers.helpers import effective, latest_transcript, load_words, source_for

router = APIRouter()


@router.get("/candidates")
def list_candidates(video_id: int | None = None, status: str | None = None, min_score: float | None = None,
                    favorite: bool | None = None, sort: str = "score", limit: int = 60, offset: int = 0,
                    s: Session = Depends(get_session)) -> dict[str, Any]:
    q = select(CandidateClip)
    if video_id:
        q = q.where(CandidateClip.video_id == video_id)
    if status:
        q = q.where(CandidateClip.status == status)
    else:
        q = q.where(CandidateClip.status != "rejected")
    if min_score is not None:
        q = q.where(CandidateClip.score >= min_score)
    if favorite:
        q = q.where(CandidateClip.favorite.is_(True))
    total = s.execute(select(func.count()).select_from(q.subquery())).scalar()
    order = {"score": CandidateClip.score.desc(), "newest": CandidateClip.created_at.desc(),
             "duration": CandidateClip.duration.asc(), "position": CandidateClip.start.asc()}.get(sort, CandidateClip.score.desc())
    rows = s.execute(q.order_by(order).limit(min(limit, 200)).offset(offset)).scalars().all()
    videos = {v.id: v for v in s.execute(select(Video).where(Video.id.in_({r.video_id for r in rows}))).scalars()}
    return {"total": total, "items": [candidate_dict(c, video=videos.get(c.video_id)) for c in rows]}


@router.get("/candidates/{cid}")
def get_candidate(cid: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    c = s.get(CandidateClip, cid)
    if c is None:
        raise HTTPException(404, "Candidate not found")
    return candidate_dict(c, with_scores=True, video=s.get(Video, c.video_id))


@router.post("/candidates/{cid}/generate")
def generate(cid: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    from shortforge.workers.stages import PRIORITY, create_short

    c = s.get(CandidateClip, cid)
    if c is None:
        raise HTTPException(404, "Candidate not found")
    v = s.get(Video, c.video_id)
    eff = effective(get_context().settings(), source_for(s, v))
    sid = create_short(s, c, eff["caption_preset"], eff["render_profile"], eff["reframe_mode"])
    s.commit()
    get_context().queue.enqueue("render_short", short_id=sid, video_id=c.video_id, priority=PRIORITY["render"] + 10,
                                dedupe_key=f"render:{sid}")
    return {"short_id": sid}


@router.post("/candidates/{cid}/reject")
def reject(cid: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    c = s.get(CandidateClip, cid)
    if c is None:
        raise HTTPException(404, "Candidate not found")
    c.status = "candidate" if c.status == "rejected" else "rejected"
    return candidate_dict(c)


@router.post("/candidates/{cid}/favorite")
def favorite(cid: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    c = s.get(CandidateClip, cid)
    if c is None:
        raise HTTPException(404, "Candidate not found")
    c.favorite = not c.favorite
    return candidate_dict(c)


class BoundsIn(BaseModel):
    start: float
    end: float


@router.patch("/candidates/{cid}")
def edit_bounds(cid: int, body: BoundsIn, s: Session = Depends(get_session)) -> dict[str, Any]:
    """Move boundaries; they snap into the nearest gap between words so nothing is cut mid-word."""
    c = s.get(CandidateClip, cid)
    if c is None:
        raise HTTPException(404, "Candidate not found")
    if body.end - body.start < 3:
        raise HTTPException(422, "Clip must be at least 3 seconds long.")
    t = latest_transcript(s, c.video_id)
    words = load_words(s, t) if t else []
    inside = [w for w in words if w.end > body.start + 0.05 and w.start < body.end - 0.05]
    if not inside:
        raise HTTPException(422, "No speech inside the new range.")
    first, last = inside[0], inside[-1]
    prev_end = words[first.idx - 1].end if first.idx > 0 else 0.0
    next_start = words[last.idx + 1].start if last.idx + 1 < len(words) else last.end + 1.0
    # Lead-in before the first word / natural tail after the last, never overlapping neighbours.
    c.start = round(max(prev_end + 0.03, first.start - 0.12, 0.0), 3)
    c.end = round(min(next_start - 0.03, last.end + 0.3), 3)
    c.duration = round(c.end - c.start, 3)
    c.first_word_idx, c.last_word_idx = first.idx, last.idx
    c.text = words_text(words[first.idx : last.idx + 1])
    return candidate_dict(c, with_scores=True)
