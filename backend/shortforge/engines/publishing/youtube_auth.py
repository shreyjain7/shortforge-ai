"""YouTube OAuth (installed-app / loopback flow). Tokens live in the OS credential store only.

The user supplies their own OAuth client (Google Cloud Console -> Credentials -> "Desktop app"),
so ShortForge never ships or embeds any secret.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any

from shortforge.core import secrets
from shortforge.core.errors import ShortForgeError
from shortforge.core.logging import get_logger

log = get_logger("youtube.auth")

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


class AuthError(ShortForgeError):
    pass


def validate_client_config(raw: str | dict[str, Any]) -> dict[str, Any]:
    data = json.loads(raw) if isinstance(raw, str) else raw
    block = data.get("installed") or data.get("web")
    if not block or not block.get("client_id") or not block.get("client_secret"):
        raise AuthError("This is not a valid Google OAuth client file. Create a 'Desktop app' OAuth client and "
                        "download its JSON.")
    if "installed" not in data:
        data = {"installed": {**block, "redirect_uris": ["http://localhost"]}}
    data["installed"].setdefault("auth_uri", "https://accounts.google.com/o/oauth2/auth")
    data["installed"].setdefault("token_uri", "https://oauth2.googleapis.com/token")
    return data


def save_client_config(raw: str | dict[str, Any]) -> None:
    secrets.set_secret(secrets.YOUTUBE_OAUTH_CLIENT, json.dumps(validate_client_config(raw)))


def has_client_config() -> bool:
    return secrets.has_secret(secrets.YOUTUBE_OAUTH_CLIENT)


def load_credentials() -> Any | None:
    """Return valid (refreshed if needed) google Credentials, or None if not connected."""
    token = secrets.get_secret(secrets.YOUTUBE_OAUTH_TOKEN)
    if not token:
        return None
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = Credentials.from_authorized_user_info(json.loads(token), SCOPES)
    if not creds.valid and creds.refresh_token:
        try:
            creds.refresh(Request())
            secrets.set_secret(secrets.YOUTUBE_OAUTH_TOKEN, creds.to_json())
        except RefreshError as exc:
            log.warning("YouTube token refresh failed; reconnect required")
            raise AuthError("YouTube authorisation expired or was revoked. Reconnect your account.") from exc
    return creds


@dataclass
class AuthState:
    status: str = "idle"  # idle | waiting | connected | error
    message: str | None = None


_state = AuthState()
_lock = threading.Lock()


def auth_state() -> AuthState:
    return _state


def start_oauth_flow(timeout_s: int = 300) -> AuthState:
    """Open the browser for consent and wait (in a background thread) for the loopback redirect."""
    raw = secrets.get_secret(secrets.YOUTUBE_OAUTH_CLIENT)
    if not raw:
        raise AuthError("Add your Google OAuth client JSON first.")
    with _lock:
        if _state.status == "waiting":
            return _state
        _state.status, _state.message = "waiting", "Complete the sign-in in your browser"

    def run() -> None:
        from google_auth_oauthlib.flow import InstalledAppFlow

        try:
            flow = InstalledAppFlow.from_client_config(json.loads(raw), SCOPES)
            creds = flow.run_local_server(port=0, open_browser=True, timeout_seconds=timeout_s,
                                          authorization_prompt_message="",
                                          success_message="ShortForge is connected. You can close this tab.",
                                          prompt="consent", access_type="offline")
            secrets.set_secret(secrets.YOUTUBE_OAUTH_TOKEN, creds.to_json())
            _state.status, _state.message = "connected", "YouTube account connected"
        except Exception as exc:  # user closed the window, timeout, network...
            _state.status, _state.message = "error", f"Sign-in did not complete: {exc}"
            log.warning("OAuth flow failed: %s", exc)

    threading.Thread(target=run, daemon=True, name="oauth").start()
    return _state


def disconnect() -> None:
    token = secrets.get_secret(secrets.YOUTUBE_OAUTH_TOKEN)
    if token:
        try:
            import httpx

            data = json.loads(token)
            httpx.post("https://oauth2.googleapis.com/revoke", params={"token": data.get("refresh_token") or data.get("token")},
                       timeout=10)
        except Exception:
            pass
    secrets.delete_secret(secrets.YOUTUBE_OAUTH_TOKEN)
    _state.status, _state.message = "idle", None


def youtube_client(creds: Any) -> Any:
    from googleapiclient.discovery import build

    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def explain_api_error(exc: BaseException) -> str | None:
    """Plain-language fix for the setup mistakes new users hit most, or None."""
    text = str(exc)
    if any(k in text for k in ("accessNotConfigured", "SERVICE_DISABLED", "has not been used in project")):
        return ("The YouTube Data API v3 is not enabled in your Google Cloud project. Enable it at "
                "https://console.cloud.google.com/apis/library/youtube.googleapis.com, wait a minute, then try again.")
    if "youtubeSignupRequired" in text:
        return "This Google account has no YouTube channel yet. Create one on youtube.com, then try again."
    return None


def connected_channel(creds: Any) -> dict[str, Any] | None:
    resp = youtube_client(creds).channels().list(part="snippet,statistics", mine=True).execute()
    items = resp.get("items") or []
    if not items:
        return None
    it = items[0]
    return {"id": it["id"], "title": it["snippet"]["title"],
            "avatar": it["snippet"].get("thumbnails", {}).get("default", {}).get("url"),
            "subscribers": it.get("statistics", {}).get("subscriberCount")}
