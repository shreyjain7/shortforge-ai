"""OS-backed secret storage (Windows Credential Manager via ``keyring``).

Secrets never touch the SQLite database or the logs. When no OS keyring backend is available
(e.g. a headless Linux CI box), a user-only file inside the data directory is used and the
fallback is reported through :func:`backend_name`.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
from pathlib import Path

from shortforge.core.logging import get_logger

log = get_logger("secrets")

SERVICE = "ShortForgeAI"
_lock = threading.Lock()
_fallback_path: Path | None = None
_force_fallback = False


def configure_fallback(path: Path, *, force: bool = False) -> None:
    global _fallback_path, _force_fallback
    _fallback_path = path
    _force_fallback = force


def _keyring():  # type: ignore[no-untyped-def]
    if _force_fallback:
        return None
    try:
        import keyring
        from keyring.backends import fail

        kr = keyring.get_keyring()
        if isinstance(kr, fail.Keyring):
            return None
        return keyring
    except Exception:  # pragma: no cover - platform specific
        return None


def backend_name() -> str:
    kr = _keyring()
    if kr is None:
        return "file-fallback"
    return type(kr.get_keyring()).__name__


def _read_fallback() -> dict[str, str]:
    if _fallback_path and _fallback_path.exists():
        try:
            return json.loads(_fallback_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def _write_fallback(data: dict[str, str]) -> None:
    if not _fallback_path:
        raise RuntimeError("No keyring backend and no fallback path configured")
    _fallback_path.parent.mkdir(parents=True, exist_ok=True)
    _fallback_path.write_text(json.dumps(data), encoding="utf-8")
    with contextlib.suppress(OSError):
        os.chmod(_fallback_path, 0o600)


# Windows Credential Manager limits a single credential blob to ~2.5 KB, and OAuth token JSON
# can exceed that, so long values are split into numbered chunks.
_CHUNK = 1000


def set_secret(name: str, value: str) -> None:
    with _lock:
        kr = _keyring()
        if kr is None:
            data = _read_fallback()
            data[name] = value
            _write_fallback(data)
            return
        delete_secret(name, _locked=True)
        chunks = [value[i : i + _CHUNK] for i in range(0, len(value), _CHUNK)] or [""]
        kr.set_password(SERVICE, f"{name}#count", str(len(chunks)))
        for idx, chunk in enumerate(chunks):
            kr.set_password(SERVICE, f"{name}#{idx}", chunk)


def get_secret(name: str) -> str | None:
    with _lock:
        kr = _keyring()
        if kr is None:
            return _read_fallback().get(name)
        count = kr.get_password(SERVICE, f"{name}#count")
        if count is None:
            return None
        parts = []
        for idx in range(int(count)):
            part = kr.get_password(SERVICE, f"{name}#{idx}")
            if part is None:
                return None
            parts.append(part)
        return "".join(parts)


def delete_secret(name: str, *, _locked: bool = False) -> None:
    def _do() -> None:
        kr = _keyring()
        if kr is None:
            data = _read_fallback()
            if name in data:
                del data[name]
                _write_fallback(data)
            return
        count = kr.get_password(SERVICE, f"{name}#count")
        if count is None:
            return
        for idx in range(int(count)):
            with contextlib.suppress(Exception):
                kr.delete_password(SERVICE, f"{name}#{idx}")
        with contextlib.suppress(Exception):
            kr.delete_password(SERVICE, f"{name}#count")

    if _locked:
        _do()
    else:
        with _lock:
            _do()


def has_secret(name: str) -> bool:
    return get_secret(name) is not None


# Well-known secret names
YOUTUBE_API_KEY = "youtube_api_key"
YOUTUBE_OAUTH_CLIENT = "youtube_oauth_client"
YOUTUBE_OAUTH_TOKEN = "youtube_oauth_token"
