"""Collect real performance metrics for uploaded Shorts (only what the APIs actually expose)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from shortforge.core.logging import get_logger
from shortforge.engines.publishing.youtube_auth import youtube_client

log = get_logger("analytics")


def fetch_statistics(creds: Any, video_ids: list[str]) -> dict[str, dict[str, int]]:
    yt = youtube_client(creds)
    out: dict[str, dict[str, int]] = {}
    for i in range(0, len(video_ids), 50):
        resp = yt.videos().list(part="statistics", id=",".join(video_ids[i : i + 50])).execute()
        for it in resp.get("items", []):
            st = it.get("statistics", {})
            out[it["id"]] = {
                "views": int(st.get("viewCount", 0)),
                "likes": int(st["likeCount"]) if "likeCount" in st else None,
                "comments": int(st["commentCount"]) if "commentCount" in st else None,
            }
    return out


def fetch_retention(creds: Any, video_ids: list[str], since: date) -> dict[str, dict[str, float]]:
    """YouTube Analytics API: average view duration/percentage. Returns {} if the API is unavailable
    (not enabled in the user's Google project, missing scope, or no data yet)."""
    if not video_ids:
        return {}
    try:
        from googleapiclient.discovery import build

        ya = build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)
        out: dict[str, dict[str, float]] = {}
        for i in range(0, len(video_ids), 200):
            chunk = video_ids[i : i + 200]
            resp = ya.reports().query(
                ids="channel==MINE", startDate=since.isoformat(), endDate=(date.today() + timedelta(days=1)).isoformat(),
                metrics="views,averageViewDuration,averageViewPercentage", dimensions="video",
                filters="video==" + ",".join(chunk), maxResults=200,
            ).execute()
            for row in resp.get("rows", []) or []:
                vid, _views, avd, avp = row[0], row[1], row[2], row[3]
                out[vid] = {"average_view_duration": float(avd), "average_view_percentage": float(avp)}
        return out
    except Exception as exc:
        log.info("YouTube Analytics API unavailable: %s", exc)
        return {}
