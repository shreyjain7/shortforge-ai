"""Composite YouTube provider.

Discovery strategy:
* Official Data API when an API key is configured (accurate publish dates, statistics, cheap quota).
* Otherwise yt-dlp for listings/metadata (no key needed).
* The public channel Atom feed for cheap update checks, which also fills in publish dates.
Downloads always go through the pluggable :class:`Downloader`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shortforge.core.errors import RetryableError, SourceResolutionError
from shortforge.core.logging import get_logger
from shortforge.engines.downloader.base import Downloader
from shortforge.engines.downloader.ytdlp_downloader import YtDlpDownloader
from shortforge.engines.youtube import rss
from shortforge.engines.youtube.data_api import DataApiClient
from shortforge.engines.youtube.provider import (
    ChannelInfo,
    ResolvedSource,
    SourceProvider,
    VideoMeta,
)
from shortforge.engines.youtube.urls import SourceKind, SourceRef, parse_source
from shortforge.engines.youtube.ytdlp_backend import YtDlpBackend

log = get_logger("youtube")


class YouTubeSourceProvider(SourceProvider):
    name = "youtube"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        js_runtime: str = "node",
        cookies_from_browser: str | None = None,
        downloader: Downloader | None = None,
        use_data_api: bool = True,
    ) -> None:
        self.ytdlp = YtDlpBackend(js_runtime, cookies_from_browser)
        self.api = DataApiClient(api_key) if (api_key and use_data_api) else None
        self.downloader = downloader or YtDlpDownloader(js_runtime, cookies_from_browser)

    # ------------------------------------------------------------------ resolution
    def resolve_source(self, text: str) -> ResolvedSource:
        ref = parse_source(text)
        if ref.kind == SourceKind.LOCAL_FILE:
            path = Path(ref.value)
            if not path.exists():
                raise SourceResolutionError(f"File not found: {path}")
            return ResolvedSource(ref=ref, title=path.stem)
        if ref.kind == SourceKind.VIDEO:
            video = self.get_video_metadata(ref.value)
            return ResolvedSource(ref=ref, title=video.title, video=video, thumbnail_url=video.thumbnail_url,
                                  recent_videos=[video], video_count=1)
        if ref.kind == SourceKind.PLAYLIST:
            info, videos = self.ytdlp.playlist_listing(ref, limit=30)
            return ResolvedSource(
                ref=ref, title=info.get("title") or ref.value, playlist_id=ref.value,
                thumbnail_url=videos[0].thumbnail_url if videos else None, recent_videos=videos,
                video_count=info.get("playlist_count"),
            )
        channel, videos, count = self._channel_and_uploads(ref, limit=12)
        return ResolvedSource(
            ref=ref, title=channel.name, channel=channel, thumbnail_url=channel.avatar_url,
            recent_videos=videos, video_count=channel.video_count or count,
        )

    def _channel_and_uploads(self, ref: SourceRef, limit: int) -> tuple[ChannelInfo, list[VideoMeta], int | None]:
        if self.api is not None:
            try:
                channel = self.api.channel(ref)
                videos = self.api.channel_uploads(channel.id, limit)
                return channel, [v for v in videos if not self._is_short_video(v)], channel.video_count
            except SourceResolutionError as exc:
                log.info("Data API could not resolve %s (%s); using yt-dlp", ref.url, exc.message)
            except RetryableError as exc:
                log.info("Data API temporarily unavailable (%s); using yt-dlp", exc.message)
        channel, videos, count = self.ytdlp.channel_listing(ref, limit)
        self._fill_dates_from_feed(channel.id, videos)
        return channel, videos, count

    @staticmethod
    def _is_short_video(v: VideoMeta) -> bool:
        return v.duration is not None and v.duration <= 60

    def _fill_dates_from_feed(self, channel_id: str, videos: list[VideoMeta]) -> None:
        if not videos or not any(v.published_at is None for v in videos):
            return
        try:
            feed = {e.video_id: e for e in rss.fetch_feed(channel_id)}
        except RetryableError:
            return
        for v in videos:
            entry = feed.get(v.id)
            if entry and v.published_at is None:
                v.published_at = entry.published_at
                v.view_count = v.view_count or entry.view_count

    def get_channel(self, ref: SourceRef | str) -> ChannelInfo:
        ref = parse_source(ref) if isinstance(ref, str) else ref
        if self.api is not None:
            try:
                return self.api.channel(ref)
            except (SourceResolutionError, RetryableError):
                pass
        channel, _, _ = self.ytdlp.channel_listing(ref, limit=1)
        return channel

    # ------------------------------------------------------------------ listing
    def list_videos(self, ref: SourceRef, limit: int = 30) -> list[VideoMeta]:
        if ref.kind == SourceKind.VIDEO:
            return [self.get_video_metadata(ref.value)]
        if ref.kind == SourceKind.PLAYLIST:
            _, videos = self.ytdlp.playlist_listing(ref, limit)
            return videos
        if ref.kind == SourceKind.LOCAL_FILE:
            return []
        _, videos, _ = self._channel_and_uploads(ref, limit)
        return videos

    def get_video_metadata(self, video_id: str) -> VideoMeta:
        if self.api is not None:
            try:
                found = self.api.videos([video_id])
                if found:
                    return found[0]
            except (SourceResolutionError, RetryableError):
                pass
        return self.ytdlp.video(video_id)

    def check_for_updates(self, ref: SourceRef, known_ids: set[str], limit: int = 15) -> list[VideoMeta]:
        """New long-form uploads not yet in ``known_ids``, newest first.

        Channels are checked through the Atom feed first (one cheap request). The feed also
        contains Shorts, so candidates are cross-checked against the channel's long-form tab.
        """
        if not ref.is_channel_like:
            return [v for v in self.list_videos(ref, limit) if v.id not in known_ids]
        _channel, videos, _ = self._channel_and_uploads(ref, limit)
        return [v for v in videos if v.id not in known_ids]

    def feed_has_new(self, channel_id: str, known_ids: set[str]) -> bool | None:
        """True if the Atom feed shows an unknown video (None if the feed could not be read)."""
        try:
            entries = rss.fetch_feed(channel_id)
        except RetryableError:
            return None
        if not entries:
            return None
        return any(e.video_id not in known_ids for e in entries)

    # ------------------------------------------------------------------ download
    def download_video(self, video: VideoMeta, dest_dir: Path, **kwargs: Any) -> Any:
        return self.downloader.download(video.url, dest_dir, video.id, expected_duration=video.duration, **kwargs)
