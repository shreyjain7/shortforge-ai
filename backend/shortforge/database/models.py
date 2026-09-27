"""SQLAlchemy ORM models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """Stores naive UTC in SQLite; always returns timezone-aware UTC datetimes."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON, datetime: UTCDateTime}


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # YouTube channel id (UC...)
    name: Mapped[str] = mapped_column(String(300), default="")
    handle: Mapped[str | None] = mapped_column(String(120))
    url: Mapped[str | None] = mapped_column(String(500))
    avatar_url: Mapped[str | None] = mapped_column(Text)
    banner_url: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    subscriber_count: Mapped[int | None] = mapped_column(Integer)
    video_count: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))  # channel | handle | video | playlist | local_file
    input: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(300), default="")
    channel_id: Mapped[str | None] = mapped_column(ForeignKey("channels.id", ondelete="SET NULL"))
    playlist_id: Mapped[str | None] = mapped_column(String(64))
    thumbnail_url: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_scan: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=50)
    scan_interval_min: Mapped[int | None] = mapped_column(Integer)
    max_videos_per_scan: Mapped[int] = mapped_column(Integer, default=5)
    min_duration_s: Mapped[float | None] = mapped_column(Float)
    max_duration_s: Mapped[float | None] = mapped_column(Float)
    max_video_age_days: Mapped[int | None] = mapped_column(Integer)
    max_shorts_per_video: Mapped[int | None] = mapped_column(Integer)
    max_shorts_per_day: Mapped[int | None] = mapped_column(Integer)
    min_candidate_score: Mapped[float | None] = mapped_column(Float)
    short_min_s: Mapped[float | None] = mapped_column(Float)
    short_max_s: Mapped[float | None] = mapped_column(Float)
    preferred_style: Mapped[str | None] = mapped_column(String(60))
    reframe_mode: Mapped[str | None] = mapped_column(String(30))
    upload_destination: Mapped[str | None] = mapped_column(String(120))
    schedule_strategy: Mapped[str | None] = mapped_column(String(30))
    auto_download: Mapped[bool] = mapped_column(Boolean, default=True)
    process_existing: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="idle")
    last_error: Mapped[str | None] = mapped_column(Text)
    last_scan_at: Mapped[datetime | None] = mapped_column()
    next_scan_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    channel: Mapped[Channel | None] = relationship()
    videos: Mapped[list[Video]] = relationship(back_populates="source")


class Video(Base):
    __tablename__ = "videos"
    __table_args__ = (Index("ix_videos_source_status", "source_id", "processing_status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    youtube_id: Mapped[str | None] = mapped_column(String(32), unique=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"), index=True)
    channel_id: Mapped[str | None] = mapped_column(ForeignKey("channels.id", ondelete="SET NULL"))
    channel_name: Mapped[str | None] = mapped_column(String(300))
    title: Mapped[str] = mapped_column(String(500), default="")
    description: Mapped[str | None] = mapped_column(Text)
    thumbnail_url: Mapped[str | None] = mapped_column(Text)
    thumbnail_path: Mapped[str | None] = mapped_column(Text)
    duration_s: Mapped[float | None] = mapped_column(Float)
    published_at: Mapped[datetime | None] = mapped_column()
    source_url: Mapped[str | None] = mapped_column(Text)
    local_path: Mapped[str | None] = mapped_column(Text)
    proxy_path: Mapped[str | None] = mapped_column(Text)
    audio_path: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    fps: Mapped[float | None] = mapped_column(Float)
    file_size: Mapped[int | None] = mapped_column(Integer)
    view_count: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str | None] = mapped_column(String(16))
    tags: Mapped[list[Any] | None] = mapped_column(JSON)
    download_status: Mapped[str] = mapped_column(String(20), default="pending")
    processing_status: Mapped[str] = mapped_column(String(20), default="new")
    transcript_status: Mapped[str] = mapped_column(String(20), default="none")
    scenes_status: Mapped[str] = mapped_column(String(20), default="none")
    analysis_status: Mapped[str] = mapped_column(String(20), default="none")
    stage: Mapped[str | None] = mapped_column(String(40))
    error: Mapped[str | None] = mapped_column(Text)
    analysis: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    discovered_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    source: Mapped[Source | None] = relationship(back_populates="videos")
    files: Mapped[list[VideoFile]] = relationship(back_populates="video", cascade="all, delete-orphan")
    transcripts: Mapped[list[Transcript]] = relationship(back_populates="video", cascade="all, delete-orphan")
    scenes: Mapped[list[Scene]] = relationship(back_populates="video", cascade="all, delete-orphan",
                                               order_by="Scene.start")
    timeline_segments: Mapped[list[TimelineSegment]] = relationship(
        back_populates="video", cascade="all, delete-orphan", order_by="TimelineSegment.start"
    )
    candidates: Mapped[list[CandidateClip]] = relationship(back_populates="video", cascade="all, delete-orphan")
    shorts: Mapped[list[Short]] = relationship(back_populates="video")


class VideoFile(Base):
    __tablename__ = "video_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # source | proxy | audio | thumbnail
    path: Mapped[str] = mapped_column(Text)
    size: Mapped[int | None] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    codec: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    video: Mapped[Video] = relationship(back_populates="files")


class Download(Base):
    __tablename__ = "downloads"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    quality: Mapped[str] = mapped_column(String(20), default="1080")
    format_id: Mapped[str | None] = mapped_column(String(80))
    bytes_total: Mapped[int | None] = mapped_column(Integer)
    bytes_done: Mapped[int | None] = mapped_column(Integer)
    speed_bps: Mapped[float | None] = mapped_column(Float)
    path: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()


class Transcript(Base):
    __tablename__ = "transcripts"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    model: Mapped[str] = mapped_column(String(60))
    device: Mapped[str | None] = mapped_column(String(20))
    compute_type: Mapped[str | None] = mapped_column(String(30))
    language: Mapped[str | None] = mapped_column(String(16))
    language_probability: Mapped[float | None] = mapped_column(Float)
    duration: Mapped[float | None] = mapped_column(Float)
    text: Mapped[str] = mapped_column(Text, default="")
    segments: Mapped[list[Any]] = mapped_column(JSON, default=list)
    sentences: Mapped[list[Any]] = mapped_column(JSON, default=list)
    elapsed_s: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    video: Mapped[Video] = relationship(back_populates="transcripts")
    words: Mapped[list[TranscriptWord]] = relationship(
        back_populates="transcript", cascade="all, delete-orphan", order_by="TranscriptWord.idx"
    )


class TranscriptWord(Base):
    __tablename__ = "transcript_words"
    __table_args__ = (Index("ix_words_transcript_idx", "transcript_id", "idx"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    transcript_id: Mapped[int] = mapped_column(ForeignKey("transcripts.id", ondelete="CASCADE"))
    idx: Mapped[int] = mapped_column(Integer)
    word: Mapped[str] = mapped_column(String(200))
    start: Mapped[float] = mapped_column(Float)
    end: Mapped[float] = mapped_column(Float)
    probability: Mapped[float] = mapped_column(Float, default=1.0)
    segment_idx: Mapped[int] = mapped_column(Integer, default=0)
    sentence_idx: Mapped[int] = mapped_column(Integer, default=0)

    transcript: Mapped[Transcript] = relationship(back_populates="words")


class Scene(Base):
    __tablename__ = "scenes"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    idx: Mapped[int] = mapped_column(Integer)
    start: Mapped[float] = mapped_column(Float)
    end: Mapped[float] = mapped_column(Float)
    kind: Mapped[str] = mapped_column(String(20), default="cut")
    score: Mapped[float | None] = mapped_column(Float)

    video: Mapped[Video] = relationship(back_populates="scenes")


class TimelineSegment(Base):
    """Semantic timeline entry (INTRO, STORY, HUMOR, ...)."""

    __tablename__ = "timeline_segments"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    start: Mapped[float] = mapped_column(Float)
    end: Mapped[float] = mapped_column(Float)
    label: Mapped[str] = mapped_column(String(40))
    summary: Mapped[str | None] = mapped_column(Text)
    energy: Mapped[float | None] = mapped_column(Float)
    interest: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(20), default="llm")

    video: Mapped[Video] = relationship(back_populates="timeline_segments")


class Speaker(Base):
    __tablename__ = "speakers"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(40))
    track_id: Mapped[int | None] = mapped_column(Integer)
    speaking_time: Mapped[float | None] = mapped_column(Float)
    avg_x: Mapped[float | None] = mapped_column(Float)
    avg_y: Mapped[float | None] = mapped_column(Float)


class Face(Base):
    __tablename__ = "faces"
    __table_args__ = (Index("ix_faces_video_t", "video_id", "t"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"))
    t: Mapped[float] = mapped_column(Float)
    x: Mapped[float] = mapped_column(Float)  # normalized [0,1]
    y: Mapped[float] = mapped_column(Float)
    w: Mapped[float] = mapped_column(Float)
    h: Mapped[float] = mapped_column(Float)
    score: Mapped[float] = mapped_column(Float)
    track_id: Mapped[int | None] = mapped_column(Integer)
    mouth_activity: Mapped[float | None] = mapped_column(Float)


class DetectedObject(Base):
    __tablename__ = "objects"
    __table_args__ = (Index("ix_objects_video_t", "video_id", "t"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"))
    t: Mapped[float] = mapped_column(Float)
    label: Mapped[str] = mapped_column(String(60))
    x: Mapped[float] = mapped_column(Float)
    y: Mapped[float] = mapped_column(Float)
    w: Mapped[float] = mapped_column(Float)
    h: Mapped[float] = mapped_column(Float)
    score: Mapped[float] = mapped_column(Float)


class CandidateClip(Base):
    __tablename__ = "candidate_clips"
    __table_args__ = (Index("ix_candidates_video_score", "video_id", "score"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"))
    start: Mapped[float] = mapped_column(Float)
    end: Mapped[float] = mapped_column(Float)
    duration: Mapped[float] = mapped_column(Float)
    first_word_idx: Mapped[int | None] = mapped_column(Integer)
    last_word_idx: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text, default="")
    title: Mapped[str | None] = mapped_column(String(300))
    hook_text: Mapped[str | None] = mapped_column(String(300))
    reasoning: Mapped[str | None] = mapped_column(Text)
    labels: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    keywords: Mapped[list[Any] | None] = mapped_column(JSON)
    analysis: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    heuristic_score: Mapped[float] = mapped_column(Float, default=0.0)
    llm_score: Mapped[float | None] = mapped_column(Float)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    rank: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="candidate")
    favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    duplicate_of: Mapped[int | None] = mapped_column(Integer)
    similarity: Mapped[float | None] = mapped_column(Float)
    pass_reached: Mapped[int] = mapped_column(Integer, default=3)
    llm_model: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    video: Mapped[Video] = relationship(back_populates="candidates")
    scores: Mapped[list[ClipScore]] = relationship(back_populates="candidate", cascade="all, delete-orphan")


class ClipScore(Base):
    __tablename__ = "clip_scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidate_clips.id", ondelete="CASCADE"), index=True)
    metric: Mapped[str] = mapped_column(String(40))
    value: Mapped[float] = mapped_column(Float)
    weight: Mapped[float] = mapped_column(Float, default=0.0)
    kind: Mapped[str] = mapped_column(String(10), default="score")  # score | penalty
    source: Mapped[str] = mapped_column(String(20), default="heuristic")  # heuristic | llm | vision

    candidate: Mapped[CandidateClip] = relationship(back_populates="scores")


class CaptionStyle(Base):
    __tablename__ = "caption_styles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Short(Base):
    __tablename__ = "shorts"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("candidate_clips.id", ondelete="SET NULL"))
    video_id: Mapped[int | None] = mapped_column(ForeignKey("videos.id", ondelete="SET NULL"), index=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    description: Mapped[str | None] = mapped_column(Text)
    hashtags: Mapped[list[Any] | None] = mapped_column(JSON)
    metadata_options: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    caption_preset: Mapped[str | None] = mapped_column(String(60))
    reframe_mode: Mapped[str | None] = mapped_column(String(30))
    render_profile: Mapped[str | None] = mapped_column(String(20))
    start: Mapped[float] = mapped_column(Float, default=0.0)
    end: Mapped[float] = mapped_column(Float, default=0.0)
    duration: Mapped[float | None] = mapped_column(Float)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    score: Mapped[float | None] = mapped_column(Float)
    output_path: Mapped[str | None] = mapped_column(Text)
    thumbnail_path: Mapped[str | None] = mapped_column(Text)
    cover_path: Mapped[str | None] = mapped_column(Text)
    qc_status: Mapped[str | None] = mapped_column(String(10))
    qc_report: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    repair_attempts: Mapped[int] = mapped_column(Integer, default=0)
    fingerprint: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    video: Mapped[Video | None] = relationship(back_populates="shorts")
    candidate: Mapped[CandidateClip | None] = relationship()
    timelines: Mapped[list[EditTimeline]] = relationship(
        back_populates="short", cascade="all, delete-orphan", order_by="EditTimeline.version"
    )
    renders: Mapped[list[Render]] = relationship(back_populates="short", cascade="all, delete-orphan")
    uploads: Mapped[list[Upload]] = relationship(back_populates="short", cascade="all, delete-orphan")


class EditTimeline(Base):
    __tablename__ = "edit_timelines"

    id: Mapped[int] = mapped_column(primary_key=True)
    short_id: Mapped[int] = mapped_column(ForeignKey("shorts.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    origin: Mapped[str] = mapped_column(String(20), default="auto")  # auto | manual | repair
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    short: Mapped[Short] = relationship(back_populates="timelines")


class Render(Base):
    __tablename__ = "renders"

    id: Mapped[int] = mapped_column(primary_key=True)
    short_id: Mapped[int] = mapped_column(ForeignKey("shorts.id", ondelete="CASCADE"), index=True)
    timeline_version: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    profile: Mapped[str | None] = mapped_column(String(20))
    encoder: Mapped[str | None] = mapped_column(String(40))
    path: Mapped[str | None] = mapped_column(Text)
    file_size: Mapped[int | None] = mapped_column(Integer)
    elapsed_s: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()

    short: Mapped[Short] = relationship(back_populates="renders")


class Upload(Base):
    __tablename__ = "uploads"

    id: Mapped[int] = mapped_column(primary_key=True)
    short_id: Mapped[int] = mapped_column(ForeignKey("shorts.id", ondelete="CASCADE"), index=True)
    account: Mapped[str | None] = mapped_column(String(120))
    youtube_video_id: Mapped[str | None] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(20), default="scheduled")
    scheduled_at: Mapped[datetime | None] = mapped_column()
    publish_at: Mapped[datetime | None] = mapped_column()
    visibility: Mapped[str] = mapped_column(String(20), default="public")
    made_for_kids: Mapped[bool] = mapped_column(Boolean, default=False)
    playlist_id: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(300), default="")
    description: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[Any] | None] = mapped_column(JSON)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    bytes_sent: Mapped[int | None] = mapped_column(Integer)
    speed_bps: Mapped[float | None] = mapped_column(Float)
    processing_status: Mapped[str | None] = mapped_column(String(40))
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    short: Mapped[Short] = relationship(back_populates="uploads")


class AnalyticsSnapshot(Base):
    __tablename__ = "analytics"
    __table_args__ = (Index("ix_analytics_short_fetched", "short_id", "fetched_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    short_id: Mapped[int] = mapped_column(ForeignKey("shorts.id", ondelete="CASCADE"))
    youtube_video_id: Mapped[str] = mapped_column(String(32))
    fetched_at: Mapped[datetime] = mapped_column(default=utcnow)
    views: Mapped[int | None] = mapped_column(Integer)
    likes: Mapped[int | None] = mapped_column(Integer)
    comments: Mapped[int | None] = mapped_column(Integer)
    average_view_duration: Mapped[float | None] = mapped_column(Float)
    average_view_percentage: Mapped[float | None] = mapped_column(Float)
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class LearningFeature(Base):
    __tablename__ = "learning_features"

    id: Mapped[int] = mapped_column(primary_key=True)
    short_id: Mapped[int] = mapped_column(ForeignKey("shorts.id", ondelete="CASCADE"), unique=True)
    features: Mapped[dict[str, Any]] = mapped_column(JSON)
    performance: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_priority", "status", "priority"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    resource: Mapped[str] = mapped_column(String(20), default="cpu")
    priority: Mapped[int] = mapped_column(Integer, default=50)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    error_detail: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    dedupe_key: Mapped[str | None] = mapped_column(String(120), index=True)
    video_id: Mapped[int | None] = mapped_column(Integer, index=True)
    short_id: Mapped[int | None] = mapped_column(Integer, index=True)
    source_id: Mapped[int | None] = mapped_column(Integer)
    logs: Mapped[str] = mapped_column(Text, default="")
    not_before: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(40))
    level: Mapped[str] = mapped_column(String(10), default="info")
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str | None] = mapped_column(Text)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    link: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
