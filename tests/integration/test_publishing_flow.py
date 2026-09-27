"""Schedule -> upload queue -> upload -> analytics -> learning, with YouTube mocked at the API boundary."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from shortforge.database.models import (
    AnalyticsSnapshot,
    CandidateClip,
    ClipScore,
    Job,
    LearningFeature,
    Short,
    Upload,
    Video,
)
from shortforge.database.models import (
    EditTimeline as EditTimelineRow,
)
from shortforge.database.session import session_scope


@pytest.fixture()
def app_ctx(data_dir, monkeypatch):
    import shortforge.core.paths as paths
    from shortforge.workers import stages  # noqa: F401 - registers handlers
    from shortforge.workers.context import AppContext, set_context
    from shortforge.workers.events import EventBus
    from shortforge.workers.queue import JobQueue

    ctx = AppContext(paths.get_paths(), EventBus())
    ctx.queue = JobQueue(ctx.bus, lambda: {"network": 2, "cpu": 2, "gpu": 1, "render": 1, "io": 1})
    set_context(ctx)
    return ctx


def _make_short(tmp_path) -> int:
    out = tmp_path / "short.mp4"
    out.write_bytes(b"0" * 20_000)
    with session_scope() as s:
        v = Video(title="Source video", youtube_id="AAAAAAAAAAA", channel_name="Any Channel", download_status="done")
        s.add(v)
        s.flush()
        c = CandidateClip(video_id=v.id, start=10, end=40, duration=30, text="hello world", score=81,
                          labels={"hook_type": "question", "category": "tech"})
        s.add(c)
        s.flush()
        for metric, value in (("hook", 90.0), ("payoff", 70.0)):
            s.add(ClipScore(candidate_id=c.id, metric=metric, value=value, kind="score"))
        sh = Short(candidate_id=c.id, video_id=v.id, title="A great moment", description="desc", hashtags=["#shorts"],
                   status="ready", qc_status="PASS", output_path=str(out), duration=30, score=81, start=10, end=40)
        s.add(sh)
        s.flush()
        s.add(EditTimelineRow(short_id=sh.id, version=1, data={
            "source_path": "x", "source_width": 1920, "source_height": 1080, "ranges": [{"start": 10, "end": 40}],
            "captions": {"words": [{"text": "hello", "start": 0.1, "end": 0.4}]}}))
        return sh.id


def _drain(queue, timeout=15.0) -> None:
    async def run() -> None:
        await queue.start()
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            await asyncio.sleep(0.2)
            with session_scope() as s:
                ready = s.execute(select(Job).where(Job.status.in_(("queued", "running")))).scalars().all()
                ready = [j for j in ready if j.not_before is None or j.not_before <= datetime.now(UTC)]
            if not ready and not queue.running_jobs():
                break
        await queue.stop()

    asyncio.run(run())


def test_schedule_upload_analytics_learning(app_ctx, tmp_path, monkeypatch) -> None:
    from shortforge.core.settings_store import update_settings
    from shortforge.engines.publishing import uploader, youtube_auth
    from shortforge.workers.stages import schedule_short

    with session_scope() as s:
        update_settings(s, {"autopilot": {"schedule_strategy": "interval", "min_upload_gap_min": 60,
                                          "upload_mode": "publish_at", "default_visibility": "unlisted"}})
    sid = _make_short(tmp_path)

    upload_id = schedule_short(sid)
    assert schedule_short(sid) == upload_id  # idempotent
    with session_scope() as s:
        up = s.get(Upload, upload_id)
        assert up.status == "scheduled" and up.publish_at is not None and up.visibility == "unlisted"
        assert up.publish_at > datetime.now(UTC) + timedelta(minutes=2)
        assert "#shorts" in (up.description or "")
        assert s.get(Short, sid).status == "scheduled"

    sent = {}

    def fake_upload(creds, req, progress=None, cancel=None, max_retries=8):  # type: ignore[no-untyped-def]
        sent["req"] = req
        if progress:
            progress(uploader.UploadProgress(0.5, 10_000, 1e6))
        return "YTVIDEO0001"

    monkeypatch.setattr(youtube_auth, "load_credentials", lambda: object())
    monkeypatch.setattr(uploader, "upload_video", fake_upload)
    monkeypatch.setattr(uploader, "processing_status", lambda c, v: {"processing": "processing"})
    _drain(app_ctx.queue)
    with session_scope() as s:
        up = s.get(Upload, upload_id)
        assert up.status == "uploaded" and up.youtube_video_id == "YTVIDEO0001" and up.progress == 1.0
        assert sent["req"].publish_at == up.publish_at and sent["req"].privacy == "unlisted"
        assert s.get(Short, sid).status == "scheduled"  # publishes later on YouTube's side

    from shortforge.engines.analytics import youtube_stats

    monkeypatch.setattr(youtube_stats, "fetch_statistics", lambda creds, ids: {"YTVIDEO0001": {"views": 1234, "likes": 56, "comments": 7}})
    monkeypatch.setattr(youtube_stats, "fetch_retention", lambda creds, ids, since: {})
    app_ctx.queue.enqueue("refresh_analytics")
    _drain(app_ctx.queue)
    with session_scope() as s:
        snap = s.execute(select(AnalyticsSnapshot)).scalar()
        assert snap.views == 1234 and snap.likes == 56 and snap.average_view_duration is None  # never invented
        lf = s.execute(select(LearningFeature).where(LearningFeature.short_id == sid)).scalar()
        assert lf.features["hook_type"] == "question" and lf.features["duration"] == 30

    app_ctx.queue.enqueue("learning_update")
    _drain(app_ctx.queue)
    from shortforge.core.settings_store import get_value

    with session_scope() as s:
        state = get_value(s, "learning_state")
    assert state["samples"] == 0 or state["samples"] < 20  # too few samples: weights untouched
    assert "Need" in state["reason"]


def test_upload_without_account_fails_readably(app_ctx, tmp_path, monkeypatch) -> None:
    from shortforge.engines.publishing import youtube_auth
    from shortforge.workers.stages import schedule_short

    sid = _make_short(tmp_path)
    upload_id = schedule_short(sid)
    monkeypatch.setattr(youtube_auth, "load_credentials", lambda: None)
    _drain(app_ctx.queue)
    with session_scope() as s:
        job = s.execute(select(Job).where(Job.dedupe_key == f"upload:{upload_id}")).scalar()
        assert job.status == "failed" and "Connect a YouTube account" in job.error
