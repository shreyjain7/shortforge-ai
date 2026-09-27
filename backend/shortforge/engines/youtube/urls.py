"""Classification and normalisation of user-provided source inputs.

Accepts any public channel/handle/video/playlist -- there is no allow-list of creators.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


class SourceKind(StrEnum):
    CHANNEL = "channel"
    HANDLE = "handle"
    VIDEO = "video"
    PLAYLIST = "playlist"
    LOCAL_FILE = "local_file"


class SourceParseError(ValueError):
    pass


VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".wmv", ".flv", ".ts", ".mts", ".m2ts"}

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_PLAYLIST_ID = re.compile(r"^(?:PL|UU|LL|FL|OL|RD|UL|PU)[A-Za-z0-9_-]{10,}$")
# YouTube handles: 3-30 chars; letters (incl. non-Latin), digits, '_', '-', '.'
_HANDLE = re.compile(r"^@?([\w.\-·]{3,30})$", re.UNICODE)
_YT_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    "youtu.be", "www.youtu.be", "youtube-nocookie.com", "www.youtube-nocookie.com",
}
_CHANNEL_TABS = {"videos", "shorts", "streams", "featured", "playlists", "community", "about", "live",
                 "podcasts", "releases", "search"}


@dataclass(frozen=True)
class SourceRef:
    kind: SourceKind
    value: str
    """Video id, channel id, handle (with leading '@'), playlist id, custom-URL path or local path."""
    url: str
    """Canonical URL (or absolute path for local files)."""
    playlist_id: str | None = None
    start_time: float | None = None

    @property
    def is_channel_like(self) -> bool:
        return self.kind in (SourceKind.CHANNEL, SourceKind.HANDLE)


def video_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def channel_url(channel_id: str) -> str:
    return f"https://www.youtube.com/channel/{channel_id}"


def handle_url(handle: str) -> str:
    return f"https://www.youtube.com/@{handle.lstrip('@')}"


def playlist_url(playlist_id: str) -> str:
    return f"https://www.youtube.com/playlist?list={playlist_id}"


def _parse_time(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip().rstrip("s")
    if value.isdigit():
        return float(value)
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+))?", value)
    if m and any(m.groups()):
        h, mnt, s = (int(g) if g else 0 for g in m.groups())
        return float(h * 3600 + mnt * 60 + s)
    return None


def _looks_like_path(text: str) -> bool:
    if re.match(r"^[A-Za-z]:[\\/]", text) or text.startswith(("\\\\", "/", "./", "../", ".\\", "..\\", "~")):
        return True
    return Path(text).suffix.lower() in VIDEO_EXTENSIONS and "://" not in text


def parse_source(text: str) -> SourceRef:
    """Identify the input type of a user-supplied source string.

    >>> parse_source("@SomeCreator").kind
    <SourceKind.HANDLE: 'handle'>
    """
    raw = (text or "").strip().strip('"').strip("'").strip()
    if not raw:
        raise SourceParseError("Enter a YouTube channel, @handle, video, playlist URL or a local video file.")

    if raw.lower().startswith("file://"):
        raw = unquote(urlparse(raw).path.lstrip("/"))
    if _looks_like_path(raw):
        path = Path(raw).expanduser()
        if path.suffix.lower() not in VIDEO_EXTENSIONS:
            raise SourceParseError(f"Unsupported local file type: {path.suffix or '(none)'}")
        return SourceRef(SourceKind.LOCAL_FILE, str(path.resolve()), str(path.resolve()))

    # Bare identifiers
    if raw.startswith("@"):
        m = _HANDLE.match(raw)
        if not m:
            raise SourceParseError(f"'{raw}' is not a valid YouTube handle.")
        handle = "@" + m.group(1)
        return SourceRef(SourceKind.HANDLE, handle, handle_url(handle))
    if _CHANNEL_ID.match(raw):
        return SourceRef(SourceKind.CHANNEL, raw, channel_url(raw))
    if _PLAYLIST_ID.match(raw) and not _VIDEO_ID.match(raw):
        return SourceRef(SourceKind.PLAYLIST, raw, playlist_url(raw))

    candidate = raw if "://" in raw else "https://" + raw
    parsed = urlparse(candidate)
    host = (parsed.hostname or "").lower()
    if host not in _YT_HOSTS:
        raise SourceParseError(
            "Not a recognised YouTube URL. Paste a channel URL, @handle, video URL, playlist URL or a local file path."
        )
    query = parse_qs(parsed.query)
    parts = [unquote(p) for p in parsed.path.split("/") if p]
    list_id = query.get("list", [None])[0]
    start = _parse_time(query.get("t", [None])[0] or query.get("start", [None])[0])

    if host.endswith("youtu.be"):
        if parts and _VIDEO_ID.match(parts[0]):
            return SourceRef(SourceKind.VIDEO, parts[0], video_url(parts[0]), list_id, start)
        raise SourceParseError("Short link does not contain a valid video id.")

    if not parts:
        raise SourceParseError("The URL does not point to a channel, video or playlist.")

    head = parts[0]
    if head == "watch":
        vid = query.get("v", [None])[0]
        if vid and _VIDEO_ID.match(vid):
            return SourceRef(SourceKind.VIDEO, vid, video_url(vid), list_id, start)
        if list_id:
            return SourceRef(SourceKind.PLAYLIST, list_id, playlist_url(list_id))
        raise SourceParseError("Watch URL is missing a valid video id (v=...).")
    if head in ("shorts", "live", "embed", "v", "e") and len(parts) >= 2 and _VIDEO_ID.match(parts[1]):
        return SourceRef(SourceKind.VIDEO, parts[1], video_url(parts[1]), list_id, start)
    if head == "playlist":
        if list_id:
            return SourceRef(SourceKind.PLAYLIST, list_id, playlist_url(list_id))
        raise SourceParseError("Playlist URL is missing the list= parameter.")
    if head.startswith("@"):
        m = _HANDLE.match(head)
        if not m:
            raise SourceParseError(f"'{head}' is not a valid YouTube handle.")
        handle = "@" + m.group(1)
        return SourceRef(SourceKind.HANDLE, handle, handle_url(handle))
    if head == "channel" and len(parts) >= 2 and _CHANNEL_ID.match(parts[1]):
        return SourceRef(SourceKind.CHANNEL, parts[1], channel_url(parts[1]))
    if head in ("c", "user") and len(parts) >= 2:
        path = f"{head}/{parts[1]}"
        return SourceRef(SourceKind.CHANNEL, path, f"https://www.youtube.com/{path}")
    # Legacy vanity URLs: youtube.com/SomeName[/videos]
    if len(parts) <= 2 and (len(parts) == 1 or parts[1] in _CHANNEL_TABS) and re.fullmatch(r"[\w.\-]{2,100}", head) \
            and head not in ("results", "feed", "account", "premium", "gaming", "hashtag", "redirect"):
        return SourceRef(SourceKind.CHANNEL, head, f"https://www.youtube.com/{head}")
    raise SourceParseError("The URL does not point to a channel, video or playlist.")


def channel_videos_url(ref: SourceRef) -> str:
    """URL of the long-form uploads tab for a channel-like source."""
    base = ref.url.rstrip("/")
    return base + "/videos"


def uploads_playlist_id(channel_id: str) -> str:
    """The implicit 'uploads' playlist of a channel (UCxxxx -> UUxxxx)."""
    if not _CHANNEL_ID.match(channel_id):
        raise ValueError(f"invalid channel id: {channel_id}")
    return "UU" + channel_id[2:]
