"""In-process event bus feeding the UI over Server-Sent Events.

Workers run in threads; publishing is thread-safe and never blocks (slow subscribers drop events,
the UI re-syncs through REST on reconnect).
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._lock = threading.Lock()

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=500)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        event = {"type": event_type, "ts": time.time(), **(data or {})}
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(self._put, q, event)

    @staticmethod
    def _put(q: asyncio.Queue[dict[str, Any]], event: dict[str, Any]) -> None:
        with contextlib.suppress(asyncio.QueueFull):
            q.put_nowait(event)


bus = EventBus()
