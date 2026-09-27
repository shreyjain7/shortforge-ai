"""Source-provider abstraction.

The rest of the application only talks to :class:`SourceProvider`; which concrete ingestion
backends are used (official Data API, RSS feeds, yt-dlp) is an implementation detail.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from shortforge.engines.youtube.urls import SourceKind, SourceRef


@dataclass
class ChannelInfo:
    id: str
    name: str
    handle: str | None = None
    url: str | None = None
    avatar_url: str | None = None
    banner_url: str | None = None
    description: str | None = None
    subscriber_count: int | None = None
    video_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VideoMeta:
    id: str
    title: str
    url: str
    channel_id: str | None = None
    channel_name: str | None = None
    description: str | None = None
    duration: float | None = None
    published_at: datetime | None = None
    thumbnail_url: str | None = None
    view_count: int | None = None
    tags: list[str] = field(default_factory=list)
    width: int | None = None
    height: int | None = None
    live_status: str | None = None
    availability: str | None = None

    @property
    def is_live_or_upcoming(self) -> bool:
        return self.live_status in ("is_live", "is_upcoming", "live", "upcoming")

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["published_at"] = self.published_at.isoformat() if self.published_at else None
        return d


@dataclass
class ResolvedSource:
    ref: SourceRef
    title: str
    channel: ChannelInfo | None = None
    playlist_id: str | None = None
    video: VideoMeta | None = None
    thumbnail_url: str | None = None
    recent_videos: list[VideoMeta] = field(default_factory=list)
    video_count: int | None = None

    @property
    def kind(self) -> SourceKind:
        return self.ref.kind


class SourceProvider(ABC):
    """Contract every ingestion implementation must satisfy."""

    name: str = "base"

    @abstractmethod
    def resolve_source(self, text: str) -> ResolvedSource: ...

    @abstractmethod
    def get_channel(self, ref: SourceRef | str) -> ChannelInfo: ...

    @abstractmethod
    def list_videos(self, ref: SourceRef, limit: int = 30) -> list[VideoMeta]: ...

    @abstractmethod
    def get_video_metadata(self, video_id: str) -> VideoMeta: ...

    @abstractmethod
    def check_for_updates(self, ref: SourceRef, known_ids: set[str], limit: int = 15) -> list[VideoMeta]: ...

    @abstractmethod
    def download_video(self, video: VideoMeta, dest_dir: Path, **kwargs: Any) -> Any: ...
