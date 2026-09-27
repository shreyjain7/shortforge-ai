"""Discovery rules: direct videos bypass windows; channel uploads obey age/duration/limits; no re-downloads."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from shortforge.database.models import Job, Source, Video
from shortforge.database.session import session_scope
from shortforge.engines.youtube.provider import ChannelInfo, VideoMeta


class FakeProvider:
    def __init__(self, videos: list[VideoMeta]) -> None:
        self.videos = videos

    def list_videos(self, ref, limit=30):  # type: ignore[no-untyped-def]
        return [v for v in self.videos if ref.kind != "video" or v.id == ref.value]

    def _channel_and_uploads(self, ref, limit):  # type: ignore[no-untyped-def]
        return ChannelInfo(id="UC" + "q" * 22, name="Chan", handle="@chan"), list(self.videos), len(self.videos)

    def get_video_metadata(self, vid):  # type: ignore[no-untyped-def]
        return next(v for v in self.videos if v.id == vid)


@pytest.fixture()
def ctx(data_dir, monkeypatch):
    import shortforge.core.paths as paths
    from shortforge.workers import stages  # noqa: F401
    from shortforge.workers.context import AppContext, set_context
    from shortforge.workers.events import EventBus
    from shortforge.workers.queue import JobQueue

    c = AppContext(paths.get_paths(), EventBus())
    c.queue = JobQueue(c.bus, lambda: {"network": 1, "cpu": 1, "gpu": 1, "render": 1, "io": 1})
    set_context(c)
    return c


def _run_scan(ctx, source_id: int) -> dict:
    from shortforge.workers.queue import REGISTRY, JobContext

    job_id = ctx.queue.enqueue("scan_source", source_id=source_id)
    jc = JobContext(ctx.queue, job_id, "scan_source", {}, video_id=None, short_id=None, source_id=source_id, attempt=0)
    result = REGISTRY["scan_source"].handler(jc)
    jc.flush_followups()
    return result


def _old(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


def test_direct_video_ignores_age_and_duration(ctx, monkeypatch) -> None:
    vids = [VideoMeta(id="OLDVIDEO001", title="Old short talk", url="u", duration=90, published_at=_old(900))]
    monkeypatch.setattr(ctx, "source_provider", lambda settings=None: FakeProvider(vids))
    with session_scope() as s:
        s.add(Source(kind="video", input="https://youtu.be/OLDVIDEO001", url="https://www.youtube.com/watch?v=OLDVIDEO001",
                     max_video_age_days=30, min_duration_s=180))
        s.flush()
        sid = s.execute(select(Source.id)).scalar()
    res = _run_scan(ctx, sid)
    assert res["queued"] == 1
    with session_scope() as s:
        assert s.execute(select(Job).where(Job.type == "download_video")).scalar() is not None


def test_channel_rules_and_no_redownload(ctx, monkeypatch) -> None:
    vids = [
        VideoMeta(id="NEWLONG0001", title="new long", url="u", duration=900, published_at=_old(1)),
        VideoMeta(id="NEWLONG0002", title="new long 2", url="u", duration=800, published_at=_old(2)),
        VideoMeta(id="TOOSHORT001", title="short", url="u", duration=45, published_at=_old(1)),
        VideoMeta(id="TOOOLD00001", title="old", url="u", duration=900, published_at=_old(400)),
    ]
    monkeypatch.setattr(ctx, "source_provider", lambda settings=None: FakeProvider(vids))
    with session_scope() as s:
        s.add(Source(kind="handle", input="@chan", url="https://www.youtube.com/@chan", max_videos_per_scan=1,
                     max_video_age_days=30, min_duration_s=180, process_existing=True))
        s.flush()
        sid = s.execute(select(Source.id)).scalar()
    res = _run_scan(ctx, sid)
    assert res["discovered"] == 4 and res["queued"] == 1
    assert res["skipped"].get("older than 30 days") == 1 and res["skipped"].get("shorter than 180s") == 1
    with session_scope() as s:
        queued = s.execute(select(Video).where(Video.download_status == "queued")).scalars().all()
        assert [v.youtube_id for v in queued] == ["NEWLONG0001"]  # newest first, limited per scan
    again = _run_scan(ctx, sid)
    assert again["discovered"] == 0 and again["queued"] == 0  # already-known videos are never re-added


def test_job_for_deleted_record_stops_cleanly(ctx) -> None:
    from shortforge.core.errors import EntityGone
    from shortforge.workers.queue import REGISTRY, JobContext

    job_id = ctx.queue.enqueue("render_short", short_id=999)
    jc = JobContext(ctx.queue, job_id, "render_short", {}, video_id=None, short_id=999, source_id=None, attempt=0)
    with pytest.raises(EntityGone) as exc:
        REGISTRY["render_short"].handler(jc)
    assert "deleted" in exc.value.message
