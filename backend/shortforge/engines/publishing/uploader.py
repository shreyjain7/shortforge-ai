"""Resumable YouTube uploads via the official Data API (videos.insert)."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shortforge.core.errors import JobCancelled, RetryableError, ShortForgeError
from shortforge.core.logging import get_logger
from shortforge.engines.media.ffmpeg import CancelToken
from shortforge.engines.publishing.youtube_auth import youtube_client

log = get_logger("upload")

RETRIABLE_STATUS = {500, 502, 503, 504}
CHUNK = 8 * 1024 * 1024


@dataclass
class UploadRequest:
    path: Path
    title: str
    description: str
    tags: list[str]
    privacy: str = "public"  # public | unlisted | private
    publish_at: datetime | None = None
    made_for_kids: bool = False
    category_id: str = "22"
    playlist_id: str | None = None
    thumbnail: Path | None = None


@dataclass
class UploadProgress:
    fraction: float
    bytes_sent: int
    speed_bps: float


def build_body(req: UploadRequest) -> dict[str, Any]:
    title = req.title.strip()[:100] or "Untitled"
    # YouTube rejects '<' and '>' in titles/descriptions.
    title = title.replace("<", "").replace(">", "")
    description = req.description.replace("<", "").replace(">", "")[:4900]
    status: dict[str, Any] = {"privacyStatus": req.privacy, "selfDeclaredMadeForKids": req.made_for_kids}
    if req.publish_at is not None:
        publish = req.publish_at.astimezone(UTC)
        if publish > datetime.now(UTC):
            status["privacyStatus"] = "private"  # required for scheduled publishing
            status["publishAt"] = publish.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    tags = [t.lstrip("#")[:100] for t in req.tags if t][:30]
    return {"snippet": {"title": title, "description": description, "tags": tags, "categoryId": req.category_id},
            "status": status}


def upload_video(creds: Any, req: UploadRequest, *, progress: Callable[[UploadProgress], None] | None = None,
                 cancel: CancelToken | None = None, max_retries: int = 8) -> str:
    from googleapiclient.errors import HttpError, ResumableUploadError
    from googleapiclient.http import MediaFileUpload

    if not req.path.exists():
        raise ShortForgeError(f"Rendered file not found: {req.path}")
    yt = youtube_client(creds)
    media = MediaFileUpload(str(req.path), chunksize=CHUNK, resumable=True, mimetype="video/mp4")
    request = yt.videos().insert(part="snippet,status", body=build_body(req), media_body=media, notifySubscribers=True)
    size = req.path.stat().st_size
    response = None
    retry = 0
    t0 = time.monotonic()
    while response is None:
        if cancel and cancel.cancelled:
            raise JobCancelled()
        try:
            status, response = request.next_chunk()
            if status and progress:
                sent = int(status.resumable_progress)
                progress(UploadProgress(sent / size, sent, sent / max(0.01, time.monotonic() - t0)))
            retry = 0
        except HttpError as exc:
            code = exc.resp.status if exc.resp else 0
            text = str(exc)
            if code in RETRIABLE_STATUS:
                pass
            elif code == 403 and "quota" in text.lower():
                raise RetryableError("YouTube upload quota exceeded for today; will retry later.", detail=text) from exc
            elif code in (401,):
                raise ShortForgeError("YouTube rejected the credentials. Reconnect your account.", detail=text) from exc
            else:
                from shortforge.engines.publishing.youtube_auth import explain_api_error

                raise ShortForgeError(explain_api_error(exc) or f"YouTube rejected the upload ({code}).",
                                      detail=text) from exc
        except (ResumableUploadError, ConnectionError, TimeoutError, OSError) as exc:
            log.warning("upload chunk error: %s", exc)
        else:
            continue
        retry += 1
        if retry > max_retries:
            raise RetryableError("Upload kept failing; it will be retried later.")
        sleep = min(64, 2**retry) * random.uniform(0.6, 1.0)
        log.info("retrying upload chunk in %.1fs", sleep)
        time.sleep(sleep)
    video_id = response["id"]
    if progress:
        progress(UploadProgress(1.0, size, size / max(0.01, time.monotonic() - t0)))
    if req.playlist_id:
        try:
            yt.playlistItems().insert(part="snippet", body={"snippet": {
                "playlistId": req.playlist_id, "resourceId": {"kind": "youtube#video", "videoId": video_id}}}).execute()
        except HttpError as exc:
            log.warning("could not add to playlist: %s", exc)
    if req.thumbnail and req.thumbnail.exists():
        try:
            yt.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(req.thumbnail))).execute()
        except HttpError as exc:  # Shorts often ignore custom thumbnails / account may be unverified
            log.info("custom thumbnail not applied: %s", exc)
    return video_id


def processing_status(creds: Any, video_id: str) -> dict[str, Any]:
    resp = youtube_client(creds).videos().list(part="status,processingDetails", id=video_id).execute()
    items = resp.get("items") or []
    if not items:
        return {"status": "unknown"}
    it = items[0]
    return {"upload_status": it.get("status", {}).get("uploadStatus"),
            "privacy": it.get("status", {}).get("privacyStatus"),
            "processing": it.get("processingDetails", {}).get("processingStatus")}
