"""Autopilot supervisor: periodic channel monitoring and maintenance.

Runs inside the server's event loop. Everything it does is expressed as persistent jobs, so a
restart never loses work and the queue remains the single place to observe/cancel activity.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from shortforge.core.logging import get_logger
from shortforge.core.settings_store import get_value, set_value
from shortforge.database.models import Short, Source, Upload
from shortforge.database.session import session_scope
from shortforge.workers.context import AppContext

log = get_logger("autopilot")

TICK_S = 30


class Autopilot:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="autopilot")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        await asyncio.sleep(5)
        while True:
            try:
                await asyncio.to_thread(self.tick)
            except Exception:
                log.exception("autopilot tick failed")
            await asyncio.sleep(TICK_S)

    def _due(self, key: str, every: timedelta) -> bool:
        with session_scope() as s:
            last = get_value(s, f"autopilot.last.{key}")
            now = datetime.now(UTC)
            if last and datetime.fromisoformat(last) > now - every:
                return False
            set_value(s, f"autopilot.last.{key}", now.isoformat())
            return True

    def tick(self) -> None:
        settings = self.ctx.settings()
        queue = self.ctx.queue
        if queue is None:
            return
        now = datetime.now(UTC)
        ap = settings.autopilot
        if ap.enabled:
            with session_scope() as s:
                due = s.execute(select(Source).where(Source.enabled.is_(True), Source.auto_scan.is_(True),
                                                     Source.kind != "local_file")
                                .order_by(Source.priority.desc())).scalars().all()
                todo = [(src.id, src.priority) for src in due if src.next_scan_at is None or src.next_scan_at <= now]
            for sid, prio in todo:
                queue.enqueue("scan_source", source_id=sid, priority=55 + min(20, prio // 5), dedupe_key=f"scan:{sid}")
        # Publish status for uploads whose scheduled publish time has passed.
        with session_scope() as s:
            for up in s.execute(select(Upload).where(Upload.status == "uploaded", Upload.publish_at.is_not(None),
                                                     Upload.publish_at <= now)).scalars():
                sh = s.get(Short, up.short_id)
                if sh and sh.status == "scheduled":
                    sh.status = "published"
        from shortforge.core import secrets
        from shortforge.engines.publishing.youtube_auth import has_client_config

        connected = has_client_config() and secrets.has_secret(secrets.YOUTUBE_OAUTH_TOKEN)
        if connected and self._due("analytics", timedelta(hours=6)):
            with session_scope() as s:
                has_uploads = s.execute(select(Upload.id).where(Upload.youtube_video_id.is_not(None)).limit(1)).scalar()
            if has_uploads:
                queue.enqueue("refresh_analytics", dedupe_key="analytics")
        if settings.learning.enabled and self._due("learning", timedelta(hours=24)):
            queue.enqueue("learning_update", dedupe_key="learning")
        if settings.storage.auto_cleanup and self._due("cleanup", timedelta(hours=12)):
            queue.enqueue("storage_cleanup", dedupe_key="cleanup")
