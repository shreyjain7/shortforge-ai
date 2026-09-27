"""FastAPI application + background services (job queue, autopilot)."""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from shortforge import __version__
from shortforge.core import secrets
from shortforge.core.config import ServerConfig
from shortforge.core.errors import ShortForgeError
from shortforge.core.logging import get_logger, setup_logging
from shortforge.core.paths import get_paths, project_root
from shortforge.core.settings_store import load_settings
from shortforge.database.session import init_db, session_scope
from shortforge.workers import stages  # noqa: F401  (registers job handlers)
from shortforge.workers.autopilot import Autopilot
from shortforge.workers.context import get_context
from shortforge.workers.events import bus
from shortforge.workers.queue import JobQueue

log = get_logger("server")

ALLOWED_ORIGINS = [
    "http://localhost:1420", "http://127.0.0.1:1420", "tauri://localhost", "http://tauri.localhost",
    "https://tauri.localhost", "http://localhost:8756", "http://127.0.0.1:8756",
]


def _queue_limits() -> dict[str, int]:
    st = get_context().settings()
    return {"network": st.queue.network_concurrency, "cpu": st.queue.cpu_concurrency, "gpu": st.gpu.max_gpu_jobs,
            "render": st.queue.render_concurrency, "io": 2}


def bootstrap() -> None:
    """Paths, logging, database and secrets (shared by the server and the CLI)."""
    paths = get_paths()
    setup_logging(paths.logs, os.environ.get("SHORTFORGE_LOG_LEVEL", "INFO"))
    # Let an installed desktop shell (which does not live next to the repo) find this engine.
    try:
        import sys

        from shortforge.core.paths import write_bootstrap

        write_bootstrap({"engine_python": sys.executable, "engine_root": str(project_root())})
    except OSError:
        pass
    init_db(paths.database)
    secrets.configure_fallback(paths.root / ".secrets.json")
    with session_scope() as s:
        load_settings(s, refresh=True)
        from shortforge.core.settings_store import _apply_side_effects

        _apply_side_effects(load_settings(s))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    bootstrap()
    ctx = get_context()
    bus.bind(asyncio.get_running_loop())
    queue = JobQueue(bus, _queue_limits)
    ctx.queue = queue
    await queue.start()
    autopilot = Autopilot(ctx)
    await autopilot.start()
    log.info("ShortForge %s ready (data: %s)", __version__, ctx.paths.root)
    # Warm hardware detection off the event loop.
    asyncio.get_running_loop().run_in_executor(None, ctx.hardware)
    try:
        yield
    finally:
        await autopilot.stop()
        await queue.stop()
        log.info("ShortForge stopped")


def create_app() -> FastAPI:
    app = FastAPI(title="ShortForge AI", version=__version__, lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_credentials=False,
                       allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def require_client_header(request: Request, call_next):  # type: ignore[no-untyped-def]
        # Mutating requests must carry a custom header. Browsers cannot add it cross-origin without
        # a CORS preflight, which only our own origins pass - this blocks drive-by requests from websites.
        if (request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith("/api/")
                and request.headers.get("x-shortforge-client") is None):
            return JSONResponse({"detail": "Missing X-ShortForge-Client header"}, status_code=403)
        return await call_next(request)

    @app.exception_handler(ShortForgeError)
    async def sf_error(_: Request, exc: ShortForgeError) -> JSONResponse:
        return JSONResponse({"detail": exc.message, "extra": exc.detail}, status_code=400)

    from shortforge.api.routes import (
        candidates,
        jobs,
        media,
        models_templates,
        publishing,
        settings,
        shorts,
        sources,
        system,
        videos,
    )

    for module in (system, settings, sources, videos, candidates, shorts, jobs, publishing, models_templates, media):
        app.include_router(module.router, prefix="/api")

    dist = project_root() / "apps" / "desktop" / "dist"
    if dist.exists():
        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str) -> FileResponse:
            candidate = (dist / full_path).resolve()
            if full_path and candidate.is_file() and str(candidate).startswith(str(dist.resolve())):
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

    return app


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="ShortForge AI backend")
    cfg = ServerConfig()
    parser.add_argument("--host", default=cfg.host)
    parser.add_argument("--port", type=int, default=cfg.port)
    parser.add_argument("--data-dir", default=None)
    args = parser.parse_args()
    if args.data_dir:
        os.environ["SHORTFORGE_DATA_DIR"] = str(Path(args.data_dir).resolve())
    import uvicorn

    uvicorn.run("shortforge.server:app", host=args.host, port=args.port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
