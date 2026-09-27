"""Persistence for :class:`AppSettings` (a single JSON document in the settings table)."""

from __future__ import annotations

import threading
from typing import Any

from sqlalchemy.orm import Session

from shortforge.core.config import AppSettings, deep_merge
from shortforge.database.models import Setting

_KEY = "app"
_cache: AppSettings | None = None
_lock = threading.Lock()


def load_settings(session: Session, *, refresh: bool = False) -> AppSettings:
    global _cache
    with _lock:
        if _cache is not None and not refresh:
            return _cache.model_copy(deep=True)
        row = session.get(Setting, _KEY)
        if row is None:
            _cache = AppSettings()
        else:
            # Unknown keys from older versions are ignored; missing keys get defaults.
            merged = deep_merge(AppSettings().model_dump(), row.value or {})
            _cache = AppSettings.model_validate(merged)
        return _cache.model_copy(deep=True)


def save_settings(session: Session, settings: AppSettings) -> AppSettings:
    global _cache
    validated = AppSettings.model_validate(settings.model_dump())
    with _lock:
        row = session.get(Setting, _KEY)
        if row is None:
            session.add(Setting(key=_KEY, value=validated.model_dump()))
        else:
            row.value = validated.model_dump()
        session.flush()
        _cache = validated.model_copy(deep=True)
    _apply_side_effects(validated)
    return validated


def update_settings(session: Session, patch: dict[str, Any]) -> AppSettings:
    current = load_settings(session)
    merged = deep_merge(current.model_dump(), patch)
    return save_settings(session, AppSettings.model_validate(merged))


def get_value(session: Session, key: str, default: Any = None) -> Any:
    row = session.get(Setting, key)
    return row.value if row is not None else default


def set_value(session: Session, key: str, value: Any) -> None:
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value
    session.flush()


def invalidate_cache() -> None:
    global _cache
    with _lock:
        _cache = None


def _apply_side_effects(settings: AppSettings) -> None:
    from shortforge.engines.media.ffmpeg import set_ffmpeg_override

    set_ffmpeg_override(settings.ffmpeg_path)
