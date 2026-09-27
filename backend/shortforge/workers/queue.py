"""Persistent local job queue (SQLite-backed; survives restarts; no Redis required).

* Jobs carry status, progress, message, error, retry_count, timestamps and a bounded log.
* Concurrency is limited per resource class: network / cpu / gpu / render / io.
* Retryable failures back off exponentially; permanent failures stop immediately with a readable error.
* On start-up, jobs that were running when the app stopped are re-queued.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError

from shortforge.core.errors import DependencyMissing, JobCancelled, RetryableError, ShortForgeError
from shortforge.core.logging import get_logger, redact
from shortforge.database.models import Job
from shortforge.database.session import session_scope
from shortforge.engines.media.ffmpeg import CancelToken
from shortforge.workers.events import EventBus

log = get_logger("queue")

ACTIVE = ("queued", "running", "paused")
MAX_LOG_CHARS = 20_000


@dataclass
class JobSpec:
    handler: Callable[[JobContext], dict[str, Any] | None]
    resource: str
    max_retries: int = 3
    priority: int = 50
    label: str = ""


REGISTRY: dict[str, JobSpec] = {}


def job_handler(name: str, *, resource: str, max_retries: int = 3, priority: int = 50, label: str = ""):  # type: ignore[no-untyped-def]
    def deco(fn: Callable[[JobContext], dict[str, Any] | None]) -> Callable[[JobContext], dict[str, Any] | None]:
        REGISTRY[name] = JobSpec(fn, resource, max_retries, priority, label or name.replace("_", " ").title())
        return fn

    return deco


def job_to_dict(job: Job) -> dict[str, Any]:
    spec = REGISTRY.get(job.type)
    return {
        "id": job.id, "type": job.type, "label": spec.label if spec else job.type, "status": job.status,
        "resource": job.resource, "priority": job.priority, "progress": round(job.progress or 0.0, 4),
        "message": job.message, "error": job.error, "error_detail": job.error_detail, "retry_count": job.retry_count,
        "max_retries": job.max_retries, "video_id": job.video_id, "short_id": job.short_id,
        "source_id": job.source_id, "payload": job.payload,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "not_before": job.not_before.isoformat() if job.not_before else None,
    }


class JobContext:
    def __init__(self, queue: JobQueue, job_id: int, job_type: str, payload: dict[str, Any], *,
                 video_id: int | None, short_id: int | None, source_id: int | None, attempt: int) -> None:
        self.queue = queue
        self.job_id = job_id
        self.type = job_type
        self.payload = payload
        self.video_id = video_id
        self.short_id = short_id
        self.source_id = source_id
        self.attempt = attempt
        self.cancel = CancelToken()
        self._last_write = 0.0
        self._logs: list[str] = []
        self._pending: list[tuple[str, dict[str, Any] | None, dict[str, Any]]] = []

    # ------------------------------------------------------------------ progress
    def progress(self, fraction: float, message: str | None = None, *, force: bool = False) -> None:
        if self.cancel.cancelled:
            raise JobCancelled()
        fraction = max(0.0, min(1.0, fraction))
        now = time.monotonic()
        self.queue.bus.publish("job.progress", {"job_id": self.job_id, "progress": round(fraction, 4),
                                                "message": message, "video_id": self.video_id,
                                                "short_id": self.short_id})
        if force or now - self._last_write > 1.0:
            self._last_write = now
            cancel = False
            try:
                with session_scope() as s:
                    values: dict[str, Any] = {"progress": fraction}
                    if message:
                        values["message"] = message[:500]
                    s.execute(update(Job).where(Job.id == self.job_id).values(**values))
                    cancel = bool(s.execute(select(Job.cancel_requested).where(Job.id == self.job_id)).scalar())
            except OperationalError as exc:  # progress is best-effort; never fail a job over it
                log.debug("progress write skipped: %s", exc)
            if cancel:
                self.cancel.cancel()
                raise JobCancelled()

    def sub(self, start: float, end: float) -> Callable[[float, str], None]:
        """Progress callback mapped into [start, end] of this job."""
        return lambda f, m=None: self.progress(start + (end - start) * f, m)

    def log(self, message: str, level: str = "info") -> None:
        line = f"{datetime.now(UTC).strftime('%H:%M:%S')} {level.upper():7s} {redact(message)}"
        self._logs.append(line)
        getattr(log, level if level in ("debug", "info", "warning", "error") else "info")(
            "[job %s %s] %s", self.job_id, self.type, message)

    def flush_logs(self) -> str:
        text = "\n".join(self._logs)
        self._logs.clear()
        return text

    def enqueue(self, job_type: str, payload: dict[str, Any] | None = None, **kw: Any) -> None:
        """Queue a follow-up job. Deferred until the handler finishes successfully, so follow-ups are
        never created by failed attempts and never contend with the handler's own DB transaction."""
        self._pending.append((job_type, payload, kw))

    def flush_followups(self) -> list[int | None]:
        pending, self._pending = self._pending, []
        return [self.queue.enqueue(t, p, **kw) for t, p, kw in pending]


class JobQueue:
    def __init__(self, bus: EventBus, limits: Callable[[], dict[str, int]]) -> None:
        self.bus = bus
        self._limits = limits
        self._running: dict[int, tuple[JobContext, str]] = {}
        self._lock = threading.RLock()
        self._wake: asyncio.Event | None = None
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._executor = ThreadPoolExecutor(max_workers=12, thread_name_prefix="job")
        self.paused = False
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self._stopping = False
        if getattr(self._executor, "_shutdown", False):
            self._executor = ThreadPoolExecutor(max_workers=12, thread_name_prefix="job")
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        self.recover()
        self._task = asyncio.create_task(self._run(), name="job-queue")

    async def stop(self) -> None:
        self._stopping = True
        with self._lock:
            for ctx, _ in self._running.values():
                ctx.cancel.cancel()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._executor.shutdown(wait=False, cancel_futures=True)
        # Leave interrupted jobs queued so they resume on next start.
        with session_scope() as s:
            s.execute(update(Job).where(Job.status == "running").values(status="queued",
                                                                         message="Interrupted; will resume"))

    def recover(self) -> int:
        with session_scope() as s:
            res = s.execute(update(Job).where(Job.status == "running").values(
                status="queued", message="Resumed after restart", progress=0.0))
            count = res.rowcount or 0
        if count:
            log.info("re-queued %d interrupted job(s)", count)
        return count

    def wake(self) -> None:
        """Nudge the scheduler (safe from any thread; a no-op when the loop is stopped)."""
        loop = self._loop
        if loop is not None and self._wake is not None and not loop.is_closed():
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(self._wake.set)

    # ------------------------------------------------------------------ API
    def enqueue(self, job_type: str, payload: dict[str, Any] | None = None, *, priority: int | None = None,
                video_id: int | None = None, short_id: int | None = None, source_id: int | None = None,
                dedupe_key: str | None = None, not_before: datetime | None = None,
                max_retries: int | None = None) -> int | None:
        spec = REGISTRY.get(job_type)
        if spec is None:
            raise ValueError(f"unknown job type {job_type}")
        with session_scope() as s:
            if dedupe_key:
                existing = s.execute(select(Job.id).where(Job.dedupe_key == dedupe_key, Job.status.in_(ACTIVE))).scalar()
                if existing:
                    return existing
            job = Job(type=job_type, resource=spec.resource, priority=priority if priority is not None else spec.priority,
                      payload=payload or {}, video_id=video_id, short_id=short_id, source_id=source_id,
                      dedupe_key=dedupe_key, not_before=not_before,
                      max_retries=spec.max_retries if max_retries is None else max_retries,
                      message="Waiting in queue")
            s.add(job)
            s.flush()
            job_id = job.id
            data = job_to_dict(job)
        self.bus.publish("job.created", {"job": data})
        self.wake()
        return job_id

    def cancel(self, job_id: int) -> bool:
        with session_scope() as s:
            job = s.get(Job, job_id)
            if job is None or job.status not in ACTIVE:
                return False
            if job.status == "running":
                job.cancel_requested = True
            else:
                job.status = "cancelled"
                job.finished_at = datetime.now(UTC)
                job.message = "Cancelled"
            data = job_to_dict(job)
        with self._lock:
            running = self._running.get(job_id)
        if running:
            running[0].cancel.cancel()
        self.bus.publish("job.updated", {"job": data})
        return True

    def retry(self, job_id: int) -> bool:
        with session_scope() as s:
            job = s.get(Job, job_id)
            if job is None or job.status not in ("failed", "cancelled"):
                return False
            job.status, job.error, job.error_detail, job.progress = "queued", None, None, 0.0
            job.cancel_requested, job.not_before, job.finished_at = False, None, None
            job.retry_count = 0
            job.message = "Retry requested"
            data = job_to_dict(job)
        self.bus.publish("job.updated", {"job": data})
        self.wake()
        return True

    def set_paused(self, job_id: int, paused: bool) -> bool:
        with session_scope() as s:
            job = s.get(Job, job_id)
            if job is None:
                return False
            if paused and job.status == "queued":
                job.status, job.message = "paused", "Paused"
            elif not paused and job.status == "paused":
                job.status, job.message = "queued", "Waiting in queue"
            else:
                return False
            data = job_to_dict(job)
        self.bus.publish("job.updated", {"job": data})
        self.wake()
        return True

    def set_priority(self, job_id: int, priority: int) -> bool:
        with session_scope() as s:
            job = s.get(Job, job_id)
            if job is None:
                return False
            job.priority = max(0, min(1000, priority))
            data = job_to_dict(job)
        self.bus.publish("job.updated", {"job": data})
        return True

    def move_to_top(self, job_id: int) -> bool:
        with session_scope() as s:
            top = s.execute(select(Job.priority).where(Job.status.in_(("queued", "paused")))
                            .order_by(Job.priority.desc()).limit(1)).scalar() or 50
        return self.set_priority(job_id, top + 1)

    def pause_all(self, paused: bool) -> None:
        self.paused = paused
        self.bus.publish("queue.state", {"paused": paused})
        self.wake()

    def running_jobs(self) -> list[int]:
        with self._lock:
            return list(self._running)

    # ------------------------------------------------------------------ scheduler loop
    async def _run(self) -> None:
        assert self._wake is not None
        while not self._stopping:
            try:
                if not self.paused:
                    self._dispatch()
            except Exception:  # never let the scheduler die
                log.exception("queue dispatch error")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=2.0)
            self._wake.clear()

    def _dispatch(self) -> None:
        limits = self._limits()
        with self._lock:
            busy: dict[str, int] = {}
            for _, res in self._running.values():
                busy[res] = busy.get(res, 0) + 1
        now = datetime.now(UTC)
        claimed: list[tuple[int, str, dict, int | None, int | None, int | None, int]] = []
        with session_scope() as s:
            rows = s.execute(select(Job).where(Job.status == "queued").order_by(Job.priority.desc(), Job.id.asc())
                             .limit(200)).scalars().all()
            for job in rows:
                if job.not_before and job.not_before > now:
                    continue
                if busy.get(job.resource, 0) >= limits.get(job.resource, 1):
                    continue
                if job.type not in REGISTRY:
                    job.status, job.error = "failed", f"Unknown job type {job.type}"
                    continue
                job.status = "running"
                job.started_at = now
                job.message = "Starting"
                job.cancel_requested = False
                busy[job.resource] = busy.get(job.resource, 0) + 1
                claimed.append((job.id, job.type, dict(job.payload or {}), job.video_id, job.short_id, job.source_id,
                                job.retry_count))
        for job_id, job_type, payload, vid, sid, srcid, attempt in claimed:
            ctx = JobContext(self, job_id, job_type, payload, video_id=vid, short_id=sid, source_id=srcid,
                             attempt=attempt)
            with self._lock:
                self._running[job_id] = (ctx, REGISTRY[job_type].resource)
            self.bus.publish("job.started", {"job_id": job_id, "type": job_type, "video_id": vid, "short_id": sid})
            assert self._loop is not None
            self._loop.run_in_executor(self._executor, self._execute, ctx)

    def _execute(self, ctx: JobContext) -> None:
        spec = REGISTRY[ctx.type]
        status, error, detail, result = "done", None, None, None
        retry_at: datetime | None = None
        t0 = time.monotonic()
        try:
            ctx.log(f"started (attempt {ctx.attempt + 1})")
            result = spec.handler(ctx) or {}
            ctx.flush_followups()
            ctx.log(f"finished in {time.monotonic() - t0:.1f}s")
        except JobCancelled as exc:
            status, error = "cancelled", exc.message
            ctx.log(exc.message, "warning")
        except DependencyMissing as exc:
            status, error, detail = "failed", exc.message, exc.detail
            ctx.log(exc.message, "error")
        except (RetryableError, ConnectionError, TimeoutError, OSError) as exc:
            message = exc.message if isinstance(exc, ShortForgeError) else str(exc)
            ctx.log(f"temporary failure: {message}", "warning")
            if ctx.attempt + 1 <= spec.max_retries:
                status = "queued"
                delay = min(3600, 30 * (2 ** ctx.attempt))
                retry_at = datetime.now(UTC) + timedelta(seconds=delay)
                error = f"{message} Retrying in {delay // 60 or delay}{'m' if delay >= 60 else 's'}."
            else:
                status, error = "failed", message
            detail = getattr(exc, "detail", None) or traceback.format_exc(limit=3)
        except ShortForgeError as exc:
            status, error, detail = "failed", exc.message, exc.detail
            ctx.log(f"failed: {exc.message}", "error")
        except Exception as exc:  # unexpected bug: record it, never crash the queue
            status, error, detail = "failed", f"Unexpected error: {exc}", traceback.format_exc()
            log.exception("job %s (%s) crashed", ctx.job_id, ctx.type)
        finally:
            with self._lock:
                self._running.pop(ctx.job_id, None)
        with session_scope() as s:
            job = s.get(Job, ctx.job_id)
            if job is not None:
                job.status = status
                job.error = redact(error)[:2000] if error else None
                job.error_detail = redact(detail)[-6000:] if detail else None
                job.result = result if status == "done" else job.result
                logs = (job.logs + "\n" if job.logs else "") + ctx.flush_logs()
                job.logs = logs[-MAX_LOG_CHARS:]
                if status == "queued":
                    job.retry_count += 1
                    job.not_before = retry_at
                    job.message = error
                    job.started_at = None
                else:
                    job.finished_at = datetime.now(UTC)
                    job.message = {"done": "Completed", "cancelled": "Cancelled"}.get(status, "Failed")
                    if status == "done":
                        job.progress = 1.0
                data = job_to_dict(job)
            else:
                data = {"id": ctx.job_id, "status": status}
        self.bus.publish("job.finished" if status != "queued" else "job.retry", {"job": data})
        self.wake()
