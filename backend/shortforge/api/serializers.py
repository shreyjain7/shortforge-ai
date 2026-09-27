"""ORM -> JSON serialisation for the API (explicit, so internal fields never leak by accident)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from shortforge.database.models import (
    CandidateClip,
    Channel,
    ClipScore,
    Short,
    Source,
    Upload,
    Video,
)
from shortforge.engines.ranking.weights import METRIC_LABELS


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def channel_dict(ch: Channel | None) -> dict[str, Any] | None:
    if ch is None:
        return None
    return {"id": ch.id, "name": ch.name, "handle": ch.handle, "url": ch.url, "avatar_url": ch.avatar_url,
            "banner_url": ch.banner_url, "subscriber_count": ch.subscriber_count, "video_count": ch.video_count,
            "description": (ch.description or "")[:500]}


def source_dict(src: Source, stats: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": src.id, "kind": src.kind, "input": src.input, "url": src.url, "title": src.title,
        "thumbnail_url": src.thumbnail_url, "channel": channel_dict(src.channel), "playlist_id": src.playlist_id,
        "enabled": src.enabled, "auto_scan": src.auto_scan, "priority": src.priority,
        "scan_interval_min": src.scan_interval_min, "max_videos_per_scan": src.max_videos_per_scan,
        "min_duration_s": src.min_duration_s, "max_duration_s": src.max_duration_s,
        "max_video_age_days": src.max_video_age_days, "max_shorts_per_video": src.max_shorts_per_video,
        "max_shorts_per_day": src.max_shorts_per_day, "min_candidate_score": src.min_candidate_score,
        "short_min_s": src.short_min_s, "short_max_s": src.short_max_s, "preferred_style": src.preferred_style,
        "reframe_mode": src.reframe_mode, "upload_destination": src.upload_destination,
        "schedule_strategy": src.schedule_strategy, "auto_download": src.auto_download,
        "process_existing": src.process_existing, "status": src.status, "last_error": src.last_error,
        "last_scan_at": iso(src.last_scan_at), "next_scan_at": iso(src.next_scan_at), "created_at": iso(src.created_at),
        "stats": stats or {},
    }


def media_url(kind: str, ident: int, name: str) -> str:
    return f"/api/media/{kind}/{ident}/{name}"


def video_dict(v: Video, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": v.id, "youtube_id": v.youtube_id, "source_id": v.source_id, "channel_id": v.channel_id,
        "channel_name": v.channel_name, "title": v.title, "description": (v.description or "")[:2000],
        "thumbnail_url": media_url("video", v.id, "thumbnail") if v.thumbnail_path else v.thumbnail_url,
        "duration_s": v.duration_s, "published_at": iso(v.published_at), "source_url": v.source_url,
        "has_local": bool(v.local_path and Path(v.local_path).exists()),
        "proxy_url": media_url("video", v.id, "proxy") if v.proxy_path and Path(v.proxy_path).exists() else None,
        "width": v.width, "height": v.height, "fps": v.fps, "file_size": v.file_size, "view_count": v.view_count,
        "language": v.language, "download_status": v.download_status, "processing_status": v.processing_status,
        "transcript_status": v.transcript_status, "scenes_status": v.scenes_status,
        "analysis_status": v.analysis_status, "stage": v.stage, "error": v.error, "analysis": v.analysis or {},
        "discovered_at": iso(v.discovered_at), **(extra or {}),
    }


def score_rows(scores: list[ClipScore]) -> list[dict[str, Any]]:
    return [{"metric": s.metric, "label": METRIC_LABELS.get(s.metric, s.metric), "value": s.value, "kind": s.kind,
             "source": s.source, "weight": s.weight} for s in sorted(scores, key=lambda x: (x.kind, x.metric))]


def candidate_dict(c: CandidateClip, *, with_scores: bool = False, video: Video | None = None) -> dict[str, Any]:
    d = {
        "id": c.id, "video_id": c.video_id, "start": c.start, "end": c.end, "duration": c.duration, "text": c.text,
        "title": c.title, "hook_text": c.hook_text, "reasoning": c.reasoning, "labels": c.labels or {},
        "keywords": c.keywords or [], "heuristic_score": c.heuristic_score, "llm_score": c.llm_score, "score": c.score,
        "rank": c.rank, "status": c.status, "favorite": c.favorite, "duplicate_of": c.duplicate_of,
        "similarity": c.similarity, "pass_reached": c.pass_reached, "llm_model": c.llm_model,
        "created_at": iso(c.created_at),
        "vision": {k: v for k, v in ((c.analysis or {}).get("vision") or {}).items() if k != "detections"},
        "notes": (c.analysis or {}).get("notes", []),
    }
    if with_scores:
        d["scores"] = score_rows(list(c.scores))
    else:
        top = {s.metric: s.value for s in c.scores if s.kind == "score"}
        d["metrics"] = {k: top.get(k) for k in ("hook", "standalone", "payoff", "visual_activity") if k in top}
    if video is not None:
        d["video"] = {"id": video.id, "title": video.title, "channel_name": video.channel_name,
                      "thumbnail_url": media_url("video", video.id, "thumbnail") if video.thumbnail_path else video.thumbnail_url,
                      "proxy_url": media_url("video", video.id, "proxy") if video.proxy_path else None}
    return d


def upload_dict(u: Upload) -> dict[str, Any]:
    return {"id": u.id, "short_id": u.short_id, "youtube_video_id": u.youtube_video_id, "status": u.status,
            "scheduled_at": iso(u.scheduled_at), "publish_at": iso(u.publish_at), "visibility": u.visibility,
            "made_for_kids": u.made_for_kids, "playlist_id": u.playlist_id, "title": u.title,
            "description": u.description, "tags": u.tags or [], "progress": u.progress, "bytes_sent": u.bytes_sent,
            "speed_bps": u.speed_bps, "processing_status": u.processing_status, "error": u.error,
            "attempts": u.attempts, "started_at": iso(u.started_at), "finished_at": iso(u.finished_at),
            "url": f"https://youtube.com/shorts/{u.youtube_video_id}" if u.youtube_video_id else None}


def short_dict(sh: Short, *, detail: bool = False, video: Video | None = None) -> dict[str, Any]:
    has_file = bool(sh.output_path and Path(sh.output_path).exists())
    meta = sh.metadata_options or {}
    d: dict[str, Any] = {
        "id": sh.id, "candidate_id": sh.candidate_id, "video_id": sh.video_id, "title": sh.title,
        "description": sh.description, "hashtags": sh.hashtags or [], "status": sh.status,
        "caption_preset": sh.caption_preset, "reframe_mode": sh.reframe_mode, "render_profile": sh.render_profile,
        "start": sh.start, "end": sh.end, "duration": sh.duration, "width": sh.width, "height": sh.height,
        "score": sh.score, "qc_status": sh.qc_status, "repair_attempts": sh.repair_attempts, "favorite": sh.favorite,
        "error": sh.error, "created_at": iso(sh.created_at), "updated_at": iso(sh.updated_at),
        "video_url": media_url("short", sh.id, "video") + f"?v={int(sh.updated_at.timestamp()) if sh.updated_at else 0}"
        if has_file else None,
        "cover_url": media_url("short", sh.id, "cover") if sh.cover_path and Path(sh.cover_path).exists() else None,
        "file_size": Path(sh.output_path).stat().st_size if has_file else None,
        "output_path": sh.output_path if has_file else None,
        "title_options": (meta.get("metadata") or {}).get("titles", []),
        "metadata_source": (meta.get("metadata") or {}).get("source"),
    }
    if video is not None:
        d["video"] = {"id": video.id, "title": video.title, "channel_name": video.channel_name,
                      "source_id": video.source_id}
    if detail:
        d["qc_report"] = sh.qc_report
        d["metadata"] = meta.get("metadata")
        d["render_report"] = meta.get("render_report")
        d["uploads"] = [upload_dict(u) for u in sh.uploads]
        d["renders"] = [{"id": r.id, "status": r.status, "encoder": r.encoder, "elapsed_s": r.elapsed_s,
                         "file_size": r.file_size, "timeline_version": r.timeline_version, "error": r.error,
                         "finished_at": iso(r.finished_at)} for r in sh.renders]
    return d
