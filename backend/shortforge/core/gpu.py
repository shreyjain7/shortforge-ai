"""Centralised GPU scheduling and model lifecycle management.

Targets 8 GB laptop GPUs: only one heavy model is resident at a time. Whisper is loaded,
used and unloaded before the LLM runs, and so on. NVENC encoding does not go through this
manager (it uses dedicated silicon and very little VRAM) so rendering can overlap with inference.
"""

from __future__ import annotations

import gc
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from shortforge.core.hardware import gpu_memory
from shortforge.core.logging import get_logger

log = get_logger("gpu")


@dataclass
class LoadedModel:
    name: str
    obj: Any
    vram_mb: int
    unload: Callable[[Any], None] | None
    last_used: float


class ModelLifecycleManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._gpu_lock = threading.Lock()
        self._models: dict[str, LoadedModel] = {}
        self.events: list[dict[str, Any]] = []
        self.on_event: Callable[[str, str], None] | None = None

    # --------------------------------------------------------------- exclusivity
    @contextmanager
    def gpu_session(self, label: str) -> Iterator[None]:
        """Serialise heavy GPU inference (Whisper, LLM, vision) across worker threads."""
        start = time.monotonic()
        with self._gpu_lock:
            waited = time.monotonic() - start
            if waited > 1:
                log.debug("%s waited %.1fs for the GPU", label, waited)
            yield

    # --------------------------------------------------------------- lifecycle
    def get(self, name: str) -> Any | None:
        with self._lock:
            m = self._models.get(name)
            if m:
                m.last_used = time.monotonic()
                return m.obj
            return None

    def load(self, name: str, loader: Callable[[], Any], *, vram_mb: int,
             unload: Callable[[Any], None] | None = None, exclusive: bool = True) -> Any:
        with self._lock:
            existing = self.get(name)
            if existing is not None:
                return existing
            if exclusive:
                self.unload_all(except_name=name)
            else:
                self.ensure_free(vram_mb)
            log.info("loading model %s (~%d MB VRAM)", name, vram_mb)
            obj = loader()
            self._models[name] = LoadedModel(name, obj, vram_mb, unload, time.monotonic())
            return obj

    def unload(self, name: str) -> None:
        with self._lock:
            m = self._models.pop(name, None)
        if m is None:
            return
        try:
            if m.unload:
                m.unload(m.obj)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("error unloading %s: %s", name, exc)
        del m
        gc.collect()
        log.info("unloaded model %s", name)

    def unload_all(self, except_name: str | None = None) -> None:
        with self._lock:
            names = [n for n in self._models if n != except_name]
        for n in names:
            self.unload(n)

    def ensure_free(self, needed_mb: int, safety_mb: int = 600) -> None:
        """Unload least-recently-used models until the GPU has ``needed_mb`` free."""
        mem = gpu_memory()
        if mem is None:
            return
        used, total = mem
        while total - used < needed_mb + safety_mb:
            with self._lock:
                if not self._models:
                    break
                lru = min(self._models.values(), key=lambda m: m.last_used).name
            self.unload(lru)
            mem = gpu_memory()
            if mem is None:
                break
            used, total = mem

    def loaded(self) -> list[dict[str, Any]]:
        with self._lock:
            return [{"name": m.name, "vram_mb": m.vram_mb, "idle_s": round(time.monotonic() - m.last_used, 1)}
                    for m in self._models.values()]

    def recover_from_oom(self, context: str) -> None:
        """Free every cached model after a CUDA OOM so the retry starts from a clean slate."""
        log.warning("CUDA out-of-memory during %s: releasing all models", context)
        self.unload_all()
        gc.collect()
        self.events.append({"ts": time.time(), "type": "oom_recovery", "context": context})
        if self.on_event:
            self.on_event("gpu_recovery", f"GPU memory exhausted during {context}. ShortForge released cached "
                                          "models and is retrying with lower memory settings.")


models = ModelLifecycleManager()
