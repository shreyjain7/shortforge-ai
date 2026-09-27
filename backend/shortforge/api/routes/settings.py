"""Settings, secrets (presence only), onboarding and notifications."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from shortforge.core import secrets
from shortforge.core.config import AppSettings
from shortforge.core.paths import get_paths, read_bootstrap, write_bootstrap
from shortforge.core.settings_store import load_settings, save_settings, update_settings
from shortforge.database.models import Notification
from shortforge.database.session import get_session
from shortforge.workers.context import get_context

router = APIRouter()


@router.get("/settings")
def get_settings(s: Session = Depends(get_session)) -> dict[str, Any]:
    return load_settings(s).model_dump()


@router.patch("/settings")
def patch_settings(patch: dict[str, Any], s: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        new = update_settings(s, patch)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    get_context().bus.publish("settings.updated", {})
    return new.model_dump()


@router.post("/settings/reset/{section}")
def reset_section(section: str, s: Session = Depends(get_session)) -> dict[str, Any]:
    current = load_settings(s)
    defaults = AppSettings()
    if not hasattr(defaults, section):
        raise HTTPException(404, "Unknown settings section")
    setattr(current, section, getattr(defaults, section))
    return save_settings(s, current).model_dump()


class SecretIn(BaseModel):
    value: str | None = None


ALLOWED_SECRETS = {"youtube_api_key": secrets.YOUTUBE_API_KEY}


@router.get("/secrets")
def secrets_status() -> dict[str, Any]:
    return {"backend": secrets.backend_name(),
            **{name: secrets.has_secret(key) for name, key in ALLOWED_SECRETS.items()},
            "youtube_oauth_client": secrets.has_secret(secrets.YOUTUBE_OAUTH_CLIENT),
            "youtube_connected": secrets.has_secret(secrets.YOUTUBE_OAUTH_TOKEN)}


@router.put("/secrets/{name}")
def set_secret(name: str, body: SecretIn) -> dict[str, Any]:
    key = ALLOWED_SECRETS.get(name)
    if key is None:
        raise HTTPException(404, "Unknown secret")
    if body.value:
        secrets.set_secret(key, body.value.strip())
    else:
        secrets.delete_secret(key)
    return {"name": name, "set": bool(body.value)}


class StorageIn(BaseModel):
    data_dir: str


@router.get("/storage/location")
def storage_location() -> dict[str, Any]:
    return {"current": str(get_paths().root), "configured": read_bootstrap().get("data_dir")}


@router.post("/storage/location")
def set_storage_location(body: StorageIn) -> dict[str, Any]:
    target = Path(body.data_dir).expanduser()
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".shortforge_write_test"
        probe.write_text("ok")
        probe.unlink()
    except OSError as exc:
        raise HTTPException(400, f"Cannot write to {target}: {exc}") from exc
    write_bootstrap({"data_dir": str(target.resolve())})
    return {"data_dir": str(target.resolve()), "restart_required": target.resolve() != get_paths().root}


@router.post("/onboarding/complete")
def onboarding_complete(body: dict[str, Any], s: Session = Depends(get_session)) -> dict[str, Any]:
    patch: dict[str, Any] = {"general": {"first_run_complete": True}}
    for key in ("ai_profile", "render_profile", "mode"):
        if key in body:
            patch["general"][key] = body[key]
    return update_settings(s, patch).model_dump()


@router.get("/notifications")
def notifications(limit: int = 50, s: Session = Depends(get_session)) -> list[dict[str, Any]]:
    rows = s.execute(select(Notification).order_by(Notification.id.desc()).limit(min(limit, 200))).scalars().all()
    return [{"id": n.id, "category": n.category, "level": n.level, "title": n.title, "body": n.body, "read": n.read,
             "link": n.link, "created_at": n.created_at.isoformat()} for n in rows]


@router.post("/notifications/read-all")
def notifications_read(s: Session = Depends(get_session)) -> dict[str, Any]:
    s.execute(update(Notification).where(Notification.read.is_(False)).values(read=True))
    return {"ok": True}
