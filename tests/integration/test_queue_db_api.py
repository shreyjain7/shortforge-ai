"""Database migrations, persistent job queue, recovery, and the HTTP API (external calls mocked)."""

from __future__ import annotations

import asyncio
import time

import pytest
from sqlalchemy import inspect, select

from shortforge.database.models import Job, Source
from shortforge.database.session import get_engine, session_scope


def test_migrations_create_all_tables(data_dir) -> None:
    tables = set(inspect(get_engine()).get_table_names())
    expected = {"sources", "channels", "videos", "video_files", "downloads", "transcripts", "transcript_words", "scenes",
                "speakers", "faces", "objects", "candidate_clips", "clip_scores", "shorts", "edit_timelines",
                "caption_styles", "renders", "uploads", "analytics", "learning_features", "jobs", "settings",
                "timeline_segments", "notifications", "alembic_version"}
    assert expected <= tables


def test_settings_roundtrip(data_dir) -> None:
    from shortforge.core.settings_store import invalidate_cache, load_settings, update_settings

    with session_scope() as s:
        update_settings(s, {"clips": {"min_duration": 20}, "autopilot": {"enabled": True}})
    invalidate_cache()
    with session_scope() as s:
        st = load_settings(s)
    assert st.clips.min_duration == 20 and st.autopilot.enabled and st.clips.max_duration == 60


def test_secrets_never_in_database(data_dir) -> None:
    from shortforge.core import secrets

    secrets.set_secret(secrets.YOUTUBE_API_KEY, "AIza" + "x" * 35)
    assert secrets.get_secret(secrets.YOUTUBE_API_KEY).startswith("AIza")
    raw = (data_dir / "shortforge.db").read_bytes()
    assert b"AIza" not in raw
    secrets.delete_secret(secrets.YOUTUBE_API_KEY)
    assert secrets.get_secret(secrets.YOUTUBE_API_KEY) is None


def test_log_redaction() -> None:
    from shortforge.core.logging import redact

    text = redact('token=ya29.abcdefghijk refresh_token: "1//0gabcdefghijklmnopqrstuvwxyz" key AIza' + "x" * 35)
    assert "ya29.abc" not in text and "1//0gabc" not in text and "AIza" not in text


@pytest.fixture()
def queue(data_dir):
    from shortforge.workers.events import EventBus
    from shortforge.workers.queue import REGISTRY, JobQueue, job_handler

    calls: list[int] = []

    @job_handler("test_ok", resource="cpu", max_retries=0)
    def ok(ctx):  # type: ignore[no-untyped-def]
        calls.append(ctx.job_id)
        ctx.progress(0.5, "half")
        ctx.enqueue("test_follow", {"parent": ctx.job_id})
        return {"ok": True}

    @job_handler("test_follow", resource="cpu", max_retries=0)
    def follow(ctx):  # type: ignore[no-untyped-def]
        return {"parent": ctx.payload["parent"]}

    @job_handler("test_flaky", resource="cpu", max_retries=2)
    def flaky(ctx):  # type: ignore[no-untyped-def]
        from shortforge.core.errors import RetryableError

        if ctx.attempt == 0:
            raise RetryableError("temporary")
        return {"attempt": ctx.attempt}

    @job_handler("test_fail", resource="cpu", max_retries=3)
    def fail(ctx):  # type: ignore[no-untyped-def]
        from shortforge.core.errors import ShortForgeError

        ctx.enqueue("test_follow", {"parent": -1})  # must NOT be created on failure
        raise ShortForgeError("permanent problem")

    q = JobQueue(EventBus(), lambda: {"cpu": 2, "gpu": 1, "network": 2, "render": 1, "io": 1})
    yield q, calls
    for name in ("test_ok", "test_follow", "test_flaky", "test_fail"):
        REGISTRY.pop(name, None)


def _run_until_idle(q, timeout: float = 20.0) -> None:
    async def runner() -> None:
        await q.start()
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            await asyncio.sleep(0.2)
            with session_scope() as s:
                active = s.execute(select(Job).where(Job.status.in_(("queued", "running")),
                                                     Job.not_before.is_(None))).scalars().all()
            if not active and not q.running_jobs():
                break
        await q.stop()

    asyncio.run(runner())


def test_queue_runs_jobs_and_followups(queue) -> None:
    q, _calls = queue
    jid = q.enqueue("test_ok")
    assert q.enqueue("test_ok", dedupe_key="same") == q.enqueue("test_ok", dedupe_key="same")
    _run_until_idle(q)
    with session_scope() as s:
        job = s.get(Job, jid)
        assert job.status == "done" and job.progress == 1.0 and job.result == {"ok": True}
        follows = s.execute(select(Job).where(Job.type == "test_follow")).scalars().all()
        assert follows and all(f.status == "done" for f in follows)
        assert "finished" in job.logs


def test_retry_with_backoff_then_success(queue) -> None:
    q, _ = queue
    jid = q.enqueue("test_flaky")
    _run_until_idle(q)
    with session_scope() as s:
        job = s.get(Job, jid)
        assert job.status == "queued" and job.retry_count == 1 and job.not_before is not None
        job.not_before = None  # fast-forward the back-off
    _run_until_idle(q)
    with session_scope() as s:
        assert s.get(Job, jid).status == "done"


def test_permanent_failure_has_readable_error_and_no_followups(queue) -> None:
    q, _ = queue
    jid = q.enqueue("test_fail")
    _run_until_idle(q)
    with session_scope() as s:
        job = s.get(Job, jid)
        assert job.status == "failed" and job.error == "permanent problem" and job.retry_count == 0
        assert not s.execute(select(Job).where(Job.type == "test_follow")).scalars().all()


def test_jobs_survive_restart(queue) -> None:
    q, _ = queue
    jid = q.enqueue("test_ok")
    with session_scope() as s:  # simulate a crash while the job was running
        s.get(Job, jid).status = "running"
    assert q.recover() == 1
    with session_scope() as s:
        assert s.get(Job, jid).status == "queued"
    _run_until_idle(q)
    with session_scope() as s:
        assert s.get(Job, jid).status == "done"


def test_cancel_pause_priority(queue) -> None:
    q, _ = queue
    a, b = q.enqueue("test_ok"), q.enqueue("test_ok")
    assert q.set_paused(a, True)
    assert q.cancel(b)
    assert q.move_to_top(a)
    with session_scope() as s:
        assert s.get(Job, a).status == "paused" and s.get(Job, b).status == "cancelled"
    assert q.retry(b)
    assert q.set_paused(a, False)


def test_api_end_to_end_with_mocked_youtube(data_dir, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from shortforge.engines.youtube.provider import ChannelInfo, ResolvedSource, VideoMeta
    from shortforge.engines.youtube.urls import parse_source
    from shortforge.server import create_app
    from shortforge.workers.context import AppContext, set_context

    class FakeProvider:
        def resolve_source(self, text):  # type: ignore[no-untyped-def]
            ref = parse_source(text)
            return ResolvedSource(ref=ref, title="Any Channel",
                                  channel=ChannelInfo(id="UC" + "z" * 22, name="Any Channel", handle="@any"),
                                  recent_videos=[VideoMeta(id="AAAAAAAAAAA", title="Video", url="u", duration=600)])

    import shortforge.core.paths as paths
    from shortforge.workers.events import bus

    ctx = AppContext(paths.get_paths(), bus)
    monkeypatch.setattr(ctx, "source_provider", lambda settings=None: FakeProvider())
    set_context(ctx)
    monkeypatch.setattr("shortforge.server.bootstrap", lambda: None)
    app = create_app()
    headers = {"X-ShortForge-Client": "test"}
    with TestClient(app) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        assert client.post("/api/sources", json={"input": "@any"}).status_code == 403  # missing header
        preview = client.post("/api/sources/resolve", json={"input": "@any"}, headers=headers).json()
        assert preview["kind"] == "handle" and preview["channel"]["name"] == "Any Channel"
        created = client.post("/api/sources", json={"input": "@any", "scan_now": False}, headers=headers).json()
        assert created["kind"] == "handle"
        assert client.post("/api/sources", json={"input": "youtube.com/@any"}, headers=headers).status_code == 409
        patched = client.patch(f"/api/sources/{created['id']}", json={"priority": 90, "max_shorts_per_video": 2},
                               headers=headers).json()
        assert patched["priority"] == 90 and patched["max_shorts_per_video"] == 2
        assert len(client.get("/api/sources").json()) == 1
        st = client.patch("/api/settings", json={"clips": {"max_duration": 45}}, headers=headers).json()
        assert st["clips"]["max_duration"] == 45
        assert client.get("/api/system/dashboard").json()["counts"]["sources"] == 1
        assert len(client.get("/api/templates/captions").json()) == 13
        jobs = client.get("/api/jobs").json()
        assert "items" in jobs and "stages" in jobs
        assert client.delete(f"/api/sources/{created['id']}", headers=headers).json()["ok"]
    with session_scope() as s:
        assert s.execute(select(Source)).scalars().all() == []
