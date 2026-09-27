"""Typed application settings.

Bootstrap settings (host/port/data dir) come from the environment. Everything the user can change
at runtime lives in :class:`AppSettings`, persisted as a JSON document in the ``settings`` table.
Secrets (API keys, OAuth tokens) are never stored here -- see :mod:`shortforge.core.secrets`.
"""

from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, Field

AIProfile = Literal["LOW", "BALANCED", "QUALITY"]
RenderProfile = Literal["FAST", "BALANCED", "ULTRA"]
Pacing = Literal["natural", "balanced", "aggressive"]
ReframeMode = Literal[
    "auto", "speaker", "conversation", "podcast", "presentation", "tech", "gameplay", "product", "cinematic"
]


class ServerConfig(BaseModel):
    host: str = Field(default_factory=lambda: os.environ.get("SHORTFORGE_HOST", "127.0.0.1"))
    port: int = Field(default_factory=lambda: int(os.environ.get("SHORTFORGE_PORT", "8756")))


class GeneralSettings(BaseModel):
    mode: Literal["simple", "advanced"] = "simple"
    first_run_complete: bool = False
    ai_profile: AIProfile = "BALANCED"
    render_profile: RenderProfile = "BALANCED"
    auto_generate_shorts: bool = True
    """When a video finishes analysis, automatically render its best candidates."""


class YouTubeSettings(BaseModel):
    download_quality: Literal["best", "1080", "1440", "2160"] = "1080"
    js_runtime: Literal["node", "deno", "bun", "auto"] = "node"
    cookies_from_browser: str | None = None
    use_data_api: bool = True
    """Use the official YouTube Data API for discovery when an API key is configured."""
    rate_limit_kbps: int | None = None


class TranscriptionSettings(BaseModel):
    model: str = "auto"
    device: Literal["auto", "cuda", "cpu"] = "auto"
    compute_type: str = "auto"
    language: str | None = None
    beam_size: int = 5
    batch_size: int = 8


class LLMSettings(BaseModel):
    provider: Literal["ollama", "llamacpp", "none"] = "ollama"
    ollama_url: str = "http://127.0.0.1:11434"
    llamacpp_url: str = "http://127.0.0.1:8080"
    model: str = "auto"
    temperature: float = 0.2
    unload_after_use: bool = True
    timeout_s: float = 180.0
    max_candidates: int = 16
    """Maximum number of heuristic candidates sent to the LLM for semantic ranking (PASS 4)."""


class ClipSettings(BaseModel):
    min_duration: float = 15.0
    max_duration: float = 60.0
    target_duration: float = 35.0
    max_shorts_per_video: int = 3
    min_score: float = 65.0
    duplicate_threshold: float = 0.8
    pacing: Pacing = "balanced"
    silence_trim: bool = True


class CaptionSettings(BaseModel):
    preset: str = "Bold"
    enabled: bool = True
    emphasis: bool = True
    max_words_per_page: int = 4
    uppercase: bool | None = None
    """None means: use the preset's own setting."""
    vertical_position: float | None = None


class ReframeSettings(BaseModel):
    mode: ReframeMode = "auto"
    sample_fps: float = 5.0
    min_hold_s: float = 1.2
    deadzone: float = 0.06
    punch_ins: bool = True
    max_punch_in: float = 1.12
    headroom: float = 0.12


class RenderSettings(BaseModel):
    width: int = 1080
    height: int = 1920
    codec: Literal["h264", "hevc", "av1"] = "h264"
    encoder: Literal["auto", "nvenc", "cpu"] = "auto"
    fps: Literal["auto", "30", "60"] = "auto"
    cq: int | None = None
    bitrate_kbps: int | None = None
    audio_bitrate_kbps: int = 192


class AudioSettings(BaseModel):
    target_lufs: float = -14.0
    true_peak: float = -1.5
    compressor: bool = False
    eq: bool = False
    denoise: bool = False
    fade_ms: int = 40


class MusicSettings(BaseModel):
    enabled: bool = False
    library_dir: str | None = None
    volume_db: float = -22.0
    duck_db: float = -10.0


class BrollSettings(BaseModel):
    enabled: bool = False
    mode: Literal["suggest", "auto"] = "suggest"
    library_dir: str | None = None
    max_inserts: int = 2
    insert_duration: float = 2.0


class EnhanceSettings(BaseModel):
    sharpen: bool = False
    vignette: bool = False
    contrast: bool = False
    color: bool = False
    denoise: bool = False


class SafeAreaSettings(BaseModel):
    """Fractions of the output frame that platform UI typically covers."""

    top: float = 0.08
    bottom: float = 0.22
    left: float = 0.06
    right: float = 0.14


class AutopilotSettings(BaseModel):
    enabled: bool = False
    scan_interval_min: int = 60
    min_clip_score: float = 70.0
    max_clips_per_video: int = 3
    max_uploads_per_day: int = 4
    min_upload_gap_min: int = 90
    caption_preset: str = "Bold"
    render_profile: RenderProfile = "ULTRA"
    auto_render: bool = True
    auto_upload: bool = False
    require_qc_pass: bool = True
    schedule_strategy: Literal["slots", "interval", "queue"] = "slots"
    schedule_slots: list[str] = Field(default_factory=lambda: ["10:00", "14:00", "18:00", "22:00"])
    upload_mode: Literal["publish_at", "at_slot"] = "publish_at"
    default_visibility: Literal["public", "unlisted", "private"] = "public"
    made_for_kids: bool = False
    playlist_id: str | None = None


class StorageSettings(BaseModel):
    max_cache_gb: float = 50.0
    auto_cleanup: bool = True
    keep_source_media: bool = True
    keep_proxy_media: bool = True
    keep_final_renders: bool = True
    temp_max_age_h: int = 24


class NotificationSettings(BaseModel):
    download_complete: bool = True
    video_analyzed: bool = True
    short_ready: bool = True
    render_failed: bool = True
    upload_completed: bool = True
    upload_failed: bool = True
    gpu_recovery: bool = True
    model_download: bool = True


class LearningSettings(BaseModel):
    enabled: bool = True
    min_samples: int = 20
    learning_rate: float = 0.15
    max_weight_change: float = 0.2


class GPUSettings(BaseModel):
    max_gpu_jobs: int = 1
    vram_safety_margin_mb: int = 600
    allow_cpu_fallback: bool = True


class QueueSettings(BaseModel):
    network_concurrency: int = 2
    cpu_concurrency: int = 2
    render_concurrency: int = 1
    max_retries: int = 3


class PromptSettings(BaseModel):
    """Optional overrides for the local-LLM system prompts (empty = built-in prompt)."""

    clip_ranker: str = ""
    timeline: str = ""
    metadata: str = ""


class AppSettings(BaseModel):
    general: GeneralSettings = Field(default_factory=GeneralSettings)
    youtube: YouTubeSettings = Field(default_factory=YouTubeSettings)
    transcription: TranscriptionSettings = Field(default_factory=TranscriptionSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    clips: ClipSettings = Field(default_factory=ClipSettings)
    captions: CaptionSettings = Field(default_factory=CaptionSettings)
    reframe: ReframeSettings = Field(default_factory=ReframeSettings)
    render: RenderSettings = Field(default_factory=RenderSettings)
    audio: AudioSettings = Field(default_factory=AudioSettings)
    music: MusicSettings = Field(default_factory=MusicSettings)
    broll: BrollSettings = Field(default_factory=BrollSettings)
    enhance: EnhanceSettings = Field(default_factory=EnhanceSettings)
    safe_area: SafeAreaSettings = Field(default_factory=SafeAreaSettings)
    autopilot: AutopilotSettings = Field(default_factory=AutopilotSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    notifications: NotificationSettings = Field(default_factory=NotificationSettings)
    learning: LearningSettings = Field(default_factory=LearningSettings)
    gpu: GPUSettings = Field(default_factory=GPUSettings)
    queue: QueueSettings = Field(default_factory=QueueSettings)
    prompts: PromptSettings = Field(default_factory=PromptSettings)
    ffmpeg_path: str | None = None
    min_free_disk_gb: float = 3.0


def deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out
