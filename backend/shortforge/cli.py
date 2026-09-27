"""Headless CLI: run the full pipeline on a URL/file without the desktop UI.

    shortforge run https://www.youtube.com/watch?v=VIDEO_ID --shorts 2
    shortforge run "@SomeChannel" --videos 1
    shortforge hardware
    shortforge models
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

from sqlalchemy import select


def _print(msg: str) -> None:
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


async def _run_pipeline(source: str, shorts: int, videos: int, timeout_min: float) -> int:
    from shortforge.core.settings_store import update_settings
    from shortforge.database.models import Job, Short, Source, Video
    from shortforge.database.session import session_scope
    from shortforge.engines.youtube.urls import parse_source
    from shortforge.server import _queue_limits, bootstrap
    from shortforge.workers.context import get_context
    from shortforge.workers.events import bus
    from shortforge.workers.queue import JobQueue

    bootstrap()
    ctx = get_context()
    bus.bind(asyncio.get_running_loop())
    queue = JobQueue(bus, _queue_limits)
    ctx.queue = queue
    with session_scope() as s:
        update_settings(s, {"clips": {"max_shorts_per_video": shorts}, "general": {"auto_generate_shorts": True}})
        ref = parse_source(source)
        src = s.execute(select(Source).where(Source.url == ref.url)).scalar()
        if src is None:
            src = Source(kind=ref.kind.value, input=source, url=ref.url, title=ref.value, max_videos_per_scan=videos,
                         process_existing=True, auto_scan=False)
            if ref.kind.value == "video":
                src.min_duration_s = None
                src.max_video_age_days = None
            s.add(src)
            s.flush()
        source_id = src.id
    queue.enqueue("scan_source", source_id=source_id, priority=90, dedupe_key=f"scan:{source_id}")
    await queue.start()
    t0 = time.monotonic()
    last = ""
    try:
        while time.monotonic() - t0 < timeout_min * 60:
            await asyncio.sleep(2)
            with session_scope() as s:
                active = s.execute(select(Job).where(Job.status.in_(("queued", "running")))).scalars().all()
                running = [j for j in active if j.status == "running"]
                line = " | ".join(f"{j.type}:{j.progress:.0%} {j.message or ''}"[:70] for j in running) or "idle"
                if line != last:
                    _print(f"[{time.monotonic() - t0:6.0f}s] {line}")
                    last = line
                if not active:
                    break
        with session_scope() as s:
            failed = s.execute(select(Job).where(Job.status == "failed")).scalars().all()
            for j in failed:
                _print(f"FAILED {j.type} #{j.id}: {j.error}")
            vids = s.execute(select(Video).where(Video.source_id == source_id)).scalars().all()
            out = s.execute(select(Short).where(Short.video_id.in_([v.id for v in vids]))).scalars().all()
            for sh in out:
                _print(json.dumps({"short": sh.id, "status": sh.status, "qc": sh.qc_status, "score": sh.score,
                                   "title": sh.title, "file": sh.output_path}, ensure_ascii=False))
            return 0 if out and all(sh.status in ("ready", "review", "scheduled", "published") for sh in out) else 1
    finally:
        await queue.stop()


def main() -> None:
    parser = argparse.ArgumentParser(prog="shortforge")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="process a URL/@handle/file end-to-end")
    run.add_argument("source")
    run.add_argument("--shorts", type=int, default=1)
    run.add_argument("--videos", type=int, default=1)
    run.add_argument("--timeout", type=float, default=60.0, help="minutes")
    sub.add_parser("hardware")
    sub.add_parser("models")
    install = sub.add_parser("install-model")
    install.add_argument("model_id")
    args = parser.parse_args()

    if args.cmd == "run":
        sys.exit(asyncio.run(_run_pipeline(args.source, args.shorts, args.videos, args.timeout)))
    from shortforge.server import bootstrap
    from shortforge.workers.context import get_context

    bootstrap()
    ctx = get_context()
    if args.cmd == "hardware":
        _print(json.dumps(ctx.hardware().to_dict(), indent=2))
    elif args.cmd == "models":
        for m in ctx.model_store().status():
            _print(f"{'[x]' if m['installed'] else '[ ]'} {m['id']:32s} {m['size_mb']:>6} MB  {m['purpose']}")
    elif args.cmd == "install-model":
        ctx.model_store().install(args.model_id, progress=lambda f, m: _print(f"{f:5.0%} {m}"))


if __name__ == "__main__":
    main()
