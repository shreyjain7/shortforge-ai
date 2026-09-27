"""YouTube's public per-channel Atom feed: the cheapest way to detect new uploads (15 latest)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime

import httpx

from shortforge.core.errors import RetryableError
from shortforge.engines.youtube.urls import video_url

FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}


@dataclass
class FeedEntry:
    video_id: str
    title: str
    published_at: datetime | None
    url: str
    thumbnail_url: str | None
    description: str | None
    view_count: int | None


def parse_feed(xml_text: str) -> list[FeedEntry]:
    root = ET.fromstring(xml_text)
    entries: list[FeedEntry] = []
    for entry in root.findall("atom:entry", _NS):
        vid = entry.findtext("yt:videoId", default="", namespaces=_NS)
        if not vid:
            continue
        published = entry.findtext("atom:published", default=None, namespaces=_NS)
        group = entry.find("media:group", _NS)
        thumb = desc = None
        views = None
        if group is not None:
            th = group.find("media:thumbnail", _NS)
            thumb = th.get("url") if th is not None else None
            desc = group.findtext("media:description", default=None, namespaces=_NS)
            stats = group.find("media:community/media:statistics", _NS)
            if stats is not None and stats.get("views", "").isdigit():
                views = int(stats.get("views"))
        entries.append(FeedEntry(
            video_id=vid,
            title=entry.findtext("atom:title", default="", namespaces=_NS),
            published_at=datetime.fromisoformat(published) if published else None,
            url=video_url(vid),
            thumbnail_url=thumb,
            description=desc,
            view_count=views,
        ))
    return entries


def fetch_feed(channel_id: str, client: httpx.Client | None = None) -> list[FeedEntry]:
    own = client is None
    client = client or httpx.Client(timeout=20, follow_redirects=True)
    try:
        resp = client.get(FEED_URL.format(channel_id=channel_id))
        if resp.status_code >= 500:
            raise RetryableError(f"YouTube feed temporarily unavailable ({resp.status_code}).")
        if resp.status_code != 200:
            return []
        return parse_feed(resp.text)
    except httpx.HTTPError as exc:
        raise RetryableError("Network error while checking the channel feed.", detail=str(exc)) from exc
    finally:
        if own:
            client.close()
