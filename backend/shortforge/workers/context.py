"""Shared services for job handlers and API routes."""

from __future__ import annotations

import threading
from typing import Any

from shortforge.core import secrets
from shortforge.core.config import AppSettings
from shortforge.core.gpu import models as model_manager
from shortforge.core.hardware import HardwareReport, hardware_report
from shortforge.core.logging import get_logger
from shortforge.core.model_registry import ALL_MODELS, ModelStore, vision_path
from shortforge.core.paths import DataPaths, get_paths
from shortforge.core.settings_store import load_settings
from shortforge.database.models import Notification
from shortforge.database.session import session_scope
from shortforge.engines.llm.base import LLMProvider
from shortforge.engines.llm.providers import create_provider
from shortforge.engines.vision.faces import FaceDetector
from shortforge.engines.youtube.youtube_provider import YouTubeSourceProvider
from shortforge.workers.events import EventBus, bus

log = get_logger("context")


class AppContext:
    def __init__(self, paths: DataPaths, event_bus: EventBus) -> None:
        self.paths = paths
        self.bus = event_bus
        self._detector: FaceDetector | None = None
        self._detector_lock = threading.Lock()
        self.queue = None  # set by the server (JobQueue)
        model_manager.on_event = lambda cat, body: self.notify(cat, "GPU memory recovery", body, level="warning")

    # ------------------------------------------------------------------ settings/hardware
    def settings(self) -> AppSettings:
        with session_scope() as s:
            return load_settings(s)

    def hardware(self, refresh: bool = False) -> HardwareReport:
        return hardware_report(self.paths.root, refresh=refresh)

    def model_store(self) -> ModelStore:
        return ModelStore(self.paths.models, self.settings().llm.ollama_url)

    # ------------------------------------------------------------------ providers
    def source_provider(self, settings: AppSettings | None = None) -> YouTubeSourceProvider:
        st = settings or self.settings()
        from shortforge.engines.downloader.ytdlp_downloader import YtDlpDownloader

        return YouTubeSourceProvider(
            api_key=secrets.get_secret(secrets.YOUTUBE_API_KEY),
            js_runtime=st.youtube.js_runtime,
            cookies_from_browser=st.youtube.cookies_from_browser,
            use_data_api=st.youtube.use_data_api,
            downloader=YtDlpDownloader(st.youtube.js_runtime, st.youtube.cookies_from_browser,
                                       st.youtube.rate_limit_kbps),
        )

    def llm(self, settings: AppSettings | None = None) -> LLMProvider | None:
        st = settings or self.settings()
        try:
            return create_provider(st.llm, self.hardware().recommended_llm)
        except Exception as exc:
            log.warning("LLM provider unavailable: %s", exc)
            return None

    def face_detector(self) -> FaceDetector:
        with self._detector_lock:
            if self._detector is None:
                spec = ALL_MODELS["vision:yunet"]
                self._detector = FaceDetector(vision_path(self.paths.models, spec))
                log.info("face detector backend: %s", self._detector.backend)
            return self._detector

    def reset_detector(self) -> None:
        with self._detector_lock:
            self._detector = None

    # ------------------------------------------------------------------ notifications
    def notify(self, category: str, title: str, body: str | None = None, *, level: str = "info",
               link: str | None = None) -> None:
        try:
            enabled = getattr(self.settings().notifications, category, True)
        except Exception:
            enabled = True
        with session_scope() as s:
            n = Notification(category=category, title=title[:200], body=body, level=level, link=link)
            s.add(n)
            s.flush()
            data: dict[str, Any] = {"id": n.id, "category": category, "title": n.title, "body": body, "level": level,
                                    "link": link, "created_at": n.created_at.isoformat(), "show": bool(enabled)}
        self.bus.publish("notification", {"notification": data})


_ctx: AppContext | None = None


def get_context() -> AppContext:
    global _ctx
    if _ctx is None:
        _ctx = AppContext(get_paths(), bus)
    return _ctx


def set_context(ctx: AppContext) -> None:
    global _ctx
    _ctx = ctx
