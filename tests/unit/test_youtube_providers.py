"""Provider logic with all external YouTube calls mocked."""

from datetime import UTC, datetime

import httpx
import pytest

from shortforge.core.errors import SourceResolutionError
from shortforge.engines.youtube import rss
from shortforge.engines.youtube.data_api import DataApiClient, parse_iso8601_duration
from shortforge.engines.youtube.provider import ChannelInfo, VideoMeta
from shortforge.engines.youtube.urls import SourceKind
from shortforge.engines.youtube.youtube_provider import YouTubeSourceProvider

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
 <entry>
  <yt:videoId>AAAAAAAAAAA</yt:videoId><title>First</title>
  <published>2026-09-20T10:00:00+00:00</published>
  <media:group><media:thumbnail url="https://i.ytimg.com/vi/AAAAAAAAAAA/hqdefault.jpg"/>
   <media:description>desc</media:description>
   <media:community><media:statistics views="1234"/></media:community></media:group>
 </entry>
 <entry><yt:videoId>BBBBBBBBBBB</yt:videoId><title>Second</title><published>2026-09-10T10:00:00+00:00</published></entry>
</feed>"""


def test_parse_feed() -> None:
    entries = rss.parse_feed(FEED)
    assert [e.video_id for e in entries] == ["AAAAAAAAAAA", "BBBBBBBBBBB"]
    assert entries[0].view_count == 1234
    assert entries[0].published_at == datetime(2026, 9, 20, 10, tzinfo=UTC)


@pytest.mark.parametrize(("iso", "secs"), [("PT1H2M3S", 3723), ("PT45S", 45), ("PT10M", 600), ("P1DT1S", 86401),
                                           (None, None)])
def test_iso_duration(iso, secs) -> None:
    assert parse_iso8601_duration(iso) == secs


def _api_client(handler) -> DataApiClient:
    return DataApiClient("KEY", client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_data_api_channel_by_handle() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["forHandle"] == "@creator"
        return httpx.Response(200, json={"items": [{
            "id": "UC" + "x" * 22,
            "snippet": {"title": "Creator", "customUrl": "@creator", "thumbnails": {"high": {"url": "http://a"}}},
            "statistics": {"subscriberCount": "1500", "videoCount": "42"}}]})

    from shortforge.engines.youtube.urls import parse_source

    ch = _api_client(handler).channel(parse_source("@creator"))
    assert ch.name == "Creator" and ch.subscriber_count == 1500 and ch.video_count == 42


def test_data_api_not_found() -> None:
    from shortforge.engines.youtube.urls import parse_source

    client = _api_client(lambda r: httpx.Response(200, json={"items": []}))
    with pytest.raises(SourceResolutionError):
        client.channel(parse_source("@missing"))


def test_data_api_videos() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [{
            "id": "AAAAAAAAAAA", "snippet": {"title": "T", "channelId": "UCx", "publishedAt": "2026-01-02T03:04:05Z",
                                             "liveBroadcastContent": "none"},
            "contentDetails": {"duration": "PT12M"}, "statistics": {"viewCount": "10"}}]})

    vids = _api_client(handler).videos(["AAAAAAAAAAA"])
    assert vids[0].duration == 720 and vids[0].view_count == 10 and not vids[0].is_live_or_upcoming


class FakeYtDlp:
    def __init__(self) -> None:
        self.calls = []

    def channel_listing(self, ref, limit):
        self.calls.append(("channel", ref.value, limit))
        return (ChannelInfo(id="UC" + "y" * 22, name="Any Channel", handle="@any"),
                [VideoMeta(id="AAAAAAAAAAA", title="First", url="u1", duration=600),
                 VideoMeta(id="BBBBBBBBBBB", title="Second", url="u2", duration=900)], 2)

    def video(self, vid):
        return VideoMeta(id=vid, title="Single", url="u", duration=300)

    def playlist_listing(self, ref, limit):
        return {"title": "PL", "playlist_count": 1}, [VideoMeta(id="CCCCCCCCCCC", title="P", url="u")]


def test_provider_resolves_any_channel_without_api_key(monkeypatch) -> None:
    provider = YouTubeSourceProvider(api_key=None)
    fake = FakeYtDlp()
    provider.ytdlp = fake
    monkeypatch.setattr(rss, "fetch_feed", lambda cid, client=None: rss.parse_feed(FEED))
    resolved = provider.resolve_source("https://www.youtube.com/@any")
    assert resolved.kind == SourceKind.HANDLE
    assert resolved.channel.name == "Any Channel"
    # Publish dates are back-filled from the Atom feed.
    assert resolved.recent_videos[0].published_at is not None
    updates = provider.check_for_updates(resolved.ref, {"AAAAAAAAAAA"})
    assert [v.id for v in updates] == ["BBBBBBBBBBB"]


def test_provider_video_and_playlist() -> None:
    provider = YouTubeSourceProvider(api_key=None)
    provider.ytdlp = FakeYtDlp()
    assert provider.resolve_source("https://youtu.be/AAAAAAAAAAA").video.title == "Single"
    pl = provider.resolve_source("https://www.youtube.com/playlist?list=PLabcdefghijklmnop")
    assert pl.kind == SourceKind.PLAYLIST and pl.recent_videos[0].id == "CCCCCCCCCCC"


def test_format_selection_respects_quality_cap() -> None:
    from shortforge.engines.downloader.ytdlp_downloader import format_selector, format_sort

    assert "height<=1080" in format_selector("1080")
    assert "height<=2160" in format_selector("2160")
    assert format_selector("best") == "bv*+ba/b"
    # original-language audio must win over auto-dubbed tracks
    assert format_sort("1080")[0] == "lang"


def test_missing_channel_is_permanent_not_network() -> None:
    from shortforge.core.errors import SourceResolutionError
    from shortforge.engines.youtube.ytdlp_backend import _map_error

    err = _map_error(Exception("ERROR: [youtube:tab] @nobody: Unable to download API page: HTTP Error 404: Not Found"),
                     "resolving channel")
    assert isinstance(err, SourceResolutionError) and not err.retryable and "does not exist" in err.message
    assert _map_error(Exception("Read timed out"), "resolving channel").retryable
