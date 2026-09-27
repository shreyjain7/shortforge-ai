"""Shared helpers for pipeline stages (DB <-> engine conversions, paths, effective settings)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from shortforge.core.config import AppSettings
from shortforge.core.paths import DataPaths
from shortforge.core.settings_store import get_value
from shortforge.database.models import Scene, Source, Transcript, TranscriptWord, Video
from shortforge.engines.audio.analysis import AudioFeatures
from shortforge.engines.ranking.weights import DEFAULT_WEIGHTS
from shortforge.engines.transcription.types import Sentence, Word


def utcnow() -> datetime:
    return datetime.now(UTC)


def video_cache(paths: DataPaths, video_id: int) -> Path:
    return paths.video_cache(video_id)


def audio_wav_path(paths: DataPaths, video_id: int) -> Path:
    return video_cache(paths, video_id) / "audio16k.wav"


def audio_features_path(paths: DataPaths, video_id: int) -> Path:
    return video_cache(paths, video_id) / "audio_features.npz"


def activity_path(paths: DataPaths, video_id: int) -> Path:
    return video_cache(paths, video_id) / "visual_activity.npy"


def proxy_path(paths: DataPaths, video_id: int) -> Path:
    return paths.proxies / f"video_{video_id}.mp4"


def render_path(paths: DataPaths, video_id: int | None, short_id: int, version: int) -> Path:
    folder = paths.renders / (f"video_{video_id}" if video_id else "misc")
    return folder / f"short_{short_id}_v{version}.mp4"


def latest_transcript(s: Session, video_id: int) -> Transcript | None:
    return s.execute(select(Transcript).where(Transcript.video_id == video_id).order_by(Transcript.id.desc())
                     .limit(1)).scalar()


def load_words(s: Session, transcript: Transcript) -> list[Word]:
    rows = s.execute(select(TranscriptWord).where(TranscriptWord.transcript_id == transcript.id)
                     .order_by(TranscriptWord.idx)).scalars().all()
    return [Word(r.idx, r.word, r.start, r.end, r.probability, r.segment_idx, r.sentence_idx) for r in rows]


def load_sentences(transcript: Transcript) -> list[Sentence]:
    return [Sentence(**d) for d in transcript.sentences or []]


def load_audio_features(paths: DataPaths, video_id: int) -> AudioFeatures | None:
    p = audio_features_path(paths, video_id)
    return AudioFeatures.from_npz(p) if p.exists() else None


def load_activity(paths: DataPaths, video_id: int) -> np.ndarray | None:
    p = activity_path(paths, video_id)
    return np.load(p) if p.exists() else None


def load_cuts(s: Session, video_id: int) -> list[float]:
    rows = s.execute(select(Scene.start).where(Scene.video_id == video_id).order_by(Scene.start)).scalars().all()
    return [float(x) for x in rows[1:]] if rows else []


def ranking_weights(s: Session) -> dict[str, float]:
    return {**DEFAULT_WEIGHTS, **(get_value(s, "ranking_weights", {}) or {})}


def source_for(s: Session, video: Video) -> Source | None:
    return s.get(Source, video.source_id) if video.source_id else None


def effective(settings: AppSettings, source: Source | None) -> dict[str, Any]:
    """Per-source overrides on top of global/autopilot settings."""
    ap = settings.autopilot
    use_ap = ap.enabled
    out = {
        "min_duration": settings.clips.min_duration,
        "max_duration": settings.clips.max_duration,
        "max_shorts": ap.max_clips_per_video if use_ap else settings.clips.max_shorts_per_video,
        "min_score": ap.min_clip_score if use_ap else settings.clips.min_score,
        "caption_preset": ap.caption_preset if use_ap else settings.captions.preset,
        "render_profile": ap.render_profile if use_ap else settings.general.render_profile,
        "reframe_mode": settings.reframe.mode,
        "max_shorts_per_day": None,
    }
    if source is not None:
        if source.short_min_s:
            out["min_duration"] = source.short_min_s
        if source.short_max_s:
            out["max_duration"] = source.short_max_s
        if source.max_shorts_per_video:
            out["max_shorts"] = source.max_shorts_per_video
        if source.min_candidate_score is not None:
            out["min_score"] = source.min_candidate_score
        if source.preferred_style:
            out["caption_preset"] = source.preferred_style
        if source.reframe_mode:
            out["reframe_mode"] = source.reframe_mode
        out["max_shorts_per_day"] = source.max_shorts_per_day
    return out


def quick_hash(path: Path) -> str:
    """Fast content hash: size + first/last 4 MB (dedupe for local imports)."""
    h = hashlib.sha256()
    size = path.stat().st_size
    h.update(str(size).encode())
    with path.open("rb") as fh:
        h.update(fh.read(4 * 1024 * 1024))
        if size > 8 * 1024 * 1024:
            fh.seek(-4 * 1024 * 1024, 2)
            h.update(fh.read())
    return h.hexdigest()
