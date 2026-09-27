"""yt-dlp based metadata extraction (works without any API key)."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from typing import Any

from shortforge.core.errors import RetryableError, SourceResolutionError
from shortforge.core.logging import get_logger
from shortforge.engines.youtube.provider import ChannelInfo, VideoMeta
from shortforge.engines.youtube.urls import SourceKind, SourceRef, channel_videos_url, video_url

log = get_logger("youtube.ytdlp")


class _QuietLogger:
    def debug(self, msg: str) -> None:
        if not msg.startswith("[debug] "):
            log.debug(msg)

    def info(self, msg: str) -> None:
        log.debug(msg)

    def warning(self, msg: str) -> None:
        log.info("yt-dlp: %s", msg)

    def error(self, msg: str) -> None:
        log.warning("yt-dlp: %s", msg)


def js_runtime_options(preferred: str = "node") -> dict[str, dict]:
    """yt-dlp needs an external JS runtime to solve YouTube's player challenges."""
    order = [preferred] if preferred != "auto" else []
    order += [r for r in ("deno", "node", "bun") if r not in order]
    for runtime in order:
        path = shutil.which(runtime)
        if path:
            return {runtime: {"path": path}}
    return {}


def base_options(js_runtime: str = "node", cookies_from_browser: str | None = None) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": False,
        "noprogress": True,
        "logger": _QuietLogger(),
        "socket_timeout": 30,
        "extractor_retries": 3,
        "retries": 10,
        "fragment_retries": 10,
        "ignoreerrors": False,
    }
    runtimes = js_runtime_options(js_runtime)
    if runtimes:
        opts["js_runtimes"] = runtimes
    if cookies_from_browser:
        opts["cookiesfrombrowser"] = (cookies_from_browser,)
    return opts


def _best_thumbnail(info: dict[str, Any], prefer_avatar: bool = False) -> str | None:
    thumbs = info.get("thumbnails") or []
    if prefer_avatar:
        for t in thumbs:
            if t.get("id") in ("avatar_uncropped",):
                return t.get("url")
        for t in thumbs:
            if "avatar" in str(t.get("id", "")):
                return t.get("url")
    best = None
    best_area = -1
    for t in thumbs:
        tid = str(t.get("id", ""))
        if "avatar" in tid or "banner" in tid:
            continue
        area = (t.get("width") or 0) * (t.get("height") or 0)
        if area >= best_area:
            best, best_area = t.get("url"), area
    return best or info.get("thumbnail")


def _banner(info: dict[str, Any]) -> str | None:
    for t in info.get("thumbnails") or []:
        if "banner" in str(t.get("id", "")):
            return t.get("url")
    return None


def _ts(info: dict[str, Any]) -> datetime | None:
    for key in ("timestamp", "release_timestamp"):
        if info.get(key):
            try:
                return datetime.fromtimestamp(int(info[key]), UTC)
            except (TypeError, ValueError, OSError):
                pass
    date = info.get("upload_date") or info.get("release_date")
    if date and len(str(date)) == 8:
        try:
            return datetime.strptime(str(date), "%Y%m%d").replace(tzinfo=UTC)
        except ValueError:
            pass
    return None


def video_from_info(info: dict[str, Any]) -> VideoMeta:
    vid = info.get("id") or ""
    thumb = info.get("thumbnail") or _best_thumbnail(info)
    if not thumb and vid:
        thumb = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
    return VideoMeta(
        id=vid,
        title=info.get("title") or "",
        url=info.get("webpage_url") or (video_url(vid) if vid else info.get("url", "")),
        channel_id=info.get("channel_id"),
        channel_name=info.get("channel") or info.get("uploader"),
        description=info.get("description"),
        duration=float(info["duration"]) if info.get("duration") else None,
        published_at=_ts(info),
        thumbnail_url=thumb,
        view_count=info.get("view_count"),
        tags=list(info.get("tags") or []),
        width=info.get("width"),
        height=info.get("height"),
        live_status=info.get("live_status"),
        availability=info.get("availability"),
    )


def _map_error(exc: Exception, what: str) -> Exception:
    text = str(exc)
    lowered = text.lower()
    if any(k in lowered for k in ("timed out", "temporary failure", "connection", "http error 5", "429",
                                  "unable to download api page")):
        return RetryableError(f"Network problem while {what}. Will retry.", detail=text)
    if "private video" in lowered:
        return SourceResolutionError("This video is private.", detail=text)
    if "does not exist" in lowered or "404" in lowered:
        return SourceResolutionError(f"YouTube says this {what.split()[-1]} does not exist.", detail=text)
    if "sign in to confirm" in lowered:
        return RetryableError(
            "YouTube asked to confirm you're not a bot. Configure 'cookies from browser' in Settings -> YouTube.",
            detail=text,
        )
    return SourceResolutionError(f"Could not complete {what}: {text.splitlines()[0][:300]}", detail=text)


class YtDlpBackend:
    def __init__(self, js_runtime: str = "node", cookies_from_browser: str | None = None) -> None:
        self.js_runtime = js_runtime
        self.cookies_from_browser = cookies_from_browser

    def _extract(self, url: str, what: str, **extra: Any) -> dict[str, Any]:
        import yt_dlp

        opts = base_options(self.js_runtime, self.cookies_from_browser)
        opts.update({"skip_download": True, **extra})
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except yt_dlp.utils.DownloadError as exc:
            raise _map_error(exc, what) from exc
        if not info:
            raise SourceResolutionError(f"Nothing found while {what}.")
        return ydl.sanitize_info(info)

    def channel_listing(self, ref: SourceRef, limit: int) -> tuple[ChannelInfo, list[VideoMeta], int | None]:
        """Channel metadata + most recent long-form uploads (Shorts tab excluded)."""
        info = self._extract(
            channel_videos_url(ref), "resolving channel", extract_flat="in_playlist", playlistend=max(1, limit),
        )
        channel = ChannelInfo(
            id=info.get("channel_id") or info.get("id") or "",
            name=info.get("channel") or info.get("uploader") or info.get("title") or "",
            handle=info.get("uploader_id") if str(info.get("uploader_id", "")).startswith("@") else None,
            url=info.get("channel_url") or ref.url,
            avatar_url=_best_thumbnail(info, prefer_avatar=True),
            banner_url=_banner(info),
            description=info.get("description"),
            subscriber_count=info.get("channel_follower_count"),
        )
        if not channel.id:
            raise SourceResolutionError("Could not determine the channel id for this source.")
        videos = []
        for entry in info.get("entries") or []:
            if not entry or entry.get("_type") not in (None, "url") or not entry.get("id"):
                continue
            meta = video_from_info(entry)
            meta.channel_id = meta.channel_id or channel.id
            meta.channel_name = meta.channel_name or channel.name
            videos.append(meta)
        return channel, videos, info.get("playlist_count")

    def playlist_listing(self, ref: SourceRef, limit: int) -> tuple[dict[str, Any], list[VideoMeta]]:
        info = self._extract(ref.url, "resolving playlist", extract_flat="in_playlist", playlistend=max(1, limit))
        videos = [video_from_info(e) for e in info.get("entries") or [] if e and e.get("id")]
        return info, videos

    def video(self, video_id_or_url: str) -> VideoMeta:
        url = video_id_or_url if "://" in video_id_or_url else video_url(video_id_or_url)
        info = self._extract(url, "reading video", noplaylist=True)
        return video_from_info(info)

    def resolve_kind(self, ref: SourceRef) -> SourceKind:
        return ref.kind
