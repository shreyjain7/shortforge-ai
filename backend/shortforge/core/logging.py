"""Structured rotating logs with secret redaction."""

from __future__ import annotations

import json
import logging
import logging.handlers
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

_SECRET_PATTERNS = [
    re.compile(r"ya29\.[0-9A-Za-z\-_]+"),  # Google OAuth access tokens
    re.compile(r"1//[0-9A-Za-z\-_]{20,}"),  # Google refresh tokens
    re.compile(r"AIza[0-9A-Za-z\-_]{35}"),  # Google API keys
    re.compile(r"GOCSPX-[0-9A-Za-z\-_]+"),  # Google OAuth client secrets
    re.compile(r"(?i)(\"?(?:access_token|refresh_token|client_secret|api_key|password|token)\"?\s*[:=]\s*\"?)[^\s\",&]+"),
    re.compile(r"(?i)(authorization:\s*bearer\s+)[^\s]+"),
]


def redact(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        if pattern.groups:
            text = pattern.sub(lambda m: m.group(1) + "***", text)
        else:
            text = pattern.sub("***", text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - defensive
            return True
        redacted = redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("job_id", "video_id", "short_id", "source_id"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


_configured = False


def setup_logging(log_dir: Path, level: str = "INFO") -> None:
    global _configured
    if _configured:
        return
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "shortforge.jsonl", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(JsonFormatter())
    file_handler.addFilter(RedactingFilter())

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(getattr(logging, level.upper(), logging.INFO))
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))
    console.addFilter(RedactingFilter())

    root.addHandler(file_handler)
    root.addHandler(console)
    for noisy in ("httpx", "httpcore", "urllib3", "googleapiclient.discovery_cache", "multipart", "watchfiles"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"shortforge.{name}")


def tail_log(log_dir: Path, lines: int = 200, level: str | None = None) -> list[dict]:
    path = log_dir / "shortforge.jsonl"
    if not path.exists():
        return []
    with path.open("rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        chunk = min(size, 512 * 1024)
        fh.seek(size - chunk)
        data = fh.read().decode("utf-8", errors="replace").splitlines()
    out: list[dict] = []
    for line in reversed(data):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if level and rec.get("level") != level.upper():
            continue
        out.append(rec)
        if len(out) >= lines:
            break
    return list(reversed(out))
