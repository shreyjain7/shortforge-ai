"""Official YouTube Data API v3 client (optional; requires a free API key).

Quota costs used here: channels.list = 1, playlistItems.list = 1, videos.list = 1 per call.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import httpx

from shortforge.core.errors import RetryableError, SourceResolutionError
from shortforge.core.logging import get_logger
from shortforge.engines.youtube.provider import ChannelInfo, VideoMeta
from shortforge.engines.youtube.urls import SourceKind, SourceRef, uploads_playlist_id, video_url

log = get_logger("youtube.data_api")

API = "https://www.googleapis.com/youtube/v3"


def parse_iso8601_duration(value: str | None) -> float | None:
    """'PT1H2M3S' -> 3723.0"""
    if not value:
        return None
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", value)
    if not m:
        return None
    d, h, mi, s = m.groups()
    return float(int(d or 0) * 86400 + int(h or 0) * 3600 + int(mi or 0) * 60 + float(s or 0))


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _thumb(snippet: dict[str, Any]) -> str | None:
    thumbs = snippet.get("thumbnails") or {}
    for key in ("maxres", "standard", "high", "medium", "default"):
        if key in thumbs:
            return thumbs[key].get("url")
    return None


class DataApiClient:
    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        self.api_key = api_key
        self.client = client or httpx.Client(timeout=30)

    def _get(self, endpoint: str, **params: Any) -> dict[str, Any]:
        params = {k: v for k, v in params.items() if v is not None}
        params["key"] = self.api_key
        try:
            resp = self.client.get(f"{API}/{endpoint}", params=params)
        except httpx.HTTPError as exc:
            raise RetryableError("Network error talking to the YouTube Data API.", detail=str(exc)) from exc
        if resp.status_code >= 500 or resp.status_code == 429:
            raise RetryableError(f"YouTube Data API temporary error ({resp.status_code}).")
        if resp.status_code == 403 and "quota" in resp.text.lower():
            raise RetryableError("YouTube Data API quota exhausted; falling back until the quota resets.")
        if resp.status_code >= 400:
            try:
                reason = resp.json()["error"]["message"]
            except Exception:
                reason = resp.text[:200]
            raise SourceResolutionError(f"YouTube Data API error: {reason}")
        return resp.json()

    def channel(self, ref: SourceRef) -> ChannelInfo:
        if ref.kind == SourceKind.HANDLE:
            data = self._get("channels", part="snippet,statistics,brandingSettings", forHandle=ref.value)
        elif ref.kind == SourceKind.CHANNEL and ref.value.startswith("UC"):
            data = self._get("channels", part="snippet,statistics,brandingSettings", id=ref.value)
        elif ref.kind == SourceKind.CHANNEL and ref.value.startswith("user/"):
            data = self._get("channels", part="snippet,statistics,brandingSettings",
                             forUsername=ref.value.split("/", 1)[1])
        else:
            raise SourceResolutionError("The Data API cannot resolve this custom URL directly.")
        items = data.get("items") or []
        if not items:
            raise SourceResolutionError("Channel not found.")
        item = items[0]
        sn, st = item.get("snippet", {}), item.get("statistics", {})
        banner = (item.get("brandingSettings", {}).get("image") or {}).get("bannerExternalUrl")
        return ChannelInfo(
            id=item["id"],
            name=sn.get("title", ""),
            handle=sn.get("customUrl") if str(sn.get("customUrl", "")).startswith("@") else None,
            url=f"https://www.youtube.com/channel/{item['id']}",
            avatar_url=_thumb(sn),
            banner_url=banner,
            description=sn.get("description"),
            subscriber_count=None if st.get("hiddenSubscriberCount") else int(st.get("subscriberCount", 0) or 0),
            video_count=int(st["videoCount"]) if st.get("videoCount") else None,
        )

    def playlist_video_ids(self, playlist_id: str, limit: int) -> list[str]:
        ids: list[str] = []
        token: str | None = None
        while len(ids) < limit:
            data = self._get("playlistItems", part="contentDetails", playlistId=playlist_id,
                             maxResults=min(50, limit - len(ids)), pageToken=token)
            ids += [it["contentDetails"]["videoId"] for it in data.get("items", [])]
            token = data.get("nextPageToken")
            if not token:
                break
        return ids

    def videos(self, ids: list[str]) -> list[VideoMeta]:
        out: list[VideoMeta] = []
        for i in range(0, len(ids), 50):
            chunk = ids[i : i + 50]
            data = self._get("videos", part="snippet,contentDetails,statistics,liveStreamingDetails",
                             id=",".join(chunk))
            by_id = {it["id"]: it for it in data.get("items", [])}
            for vid in chunk:
                it = by_id.get(vid)
                if not it:
                    continue
                sn, cd, st = it.get("snippet", {}), it.get("contentDetails", {}), it.get("statistics", {})
                live = sn.get("liveBroadcastContent")
                out.append(VideoMeta(
                    id=vid,
                    title=sn.get("title", ""),
                    url=video_url(vid),
                    channel_id=sn.get("channelId"),
                    channel_name=sn.get("channelTitle"),
                    description=sn.get("description"),
                    duration=parse_iso8601_duration(cd.get("duration")),
                    published_at=_parse_dt(sn.get("publishedAt")),
                    thumbnail_url=_thumb(sn),
                    view_count=int(st["viewCount"]) if st.get("viewCount") else None,
                    tags=sn.get("tags") or [],
                    live_status={"live": "is_live", "upcoming": "is_upcoming"}.get(live, "not_live"),
                ))
        return out

    def channel_uploads(self, channel_id: str, limit: int) -> list[VideoMeta]:
        return self.videos(self.playlist_video_ids(uploads_playlist_id(channel_id), limit))

    def video_statistics(self, ids: list[str]) -> dict[str, dict[str, int]]:
        stats: dict[str, dict[str, int]] = {}
        for i in range(0, len(ids), 50):
            data = self._get("videos", part="statistics", id=",".join(ids[i : i + 50]))
            for it in data.get("items", []):
                st = it.get("statistics", {})
                stats[it["id"]] = {k: int(v) for k, v in st.items() if str(v).isdigit()}
        return stats
