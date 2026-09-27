"""Pipeline stage handlers (one persistent job per stage).

discover -> download/import -> prepare (proxy, audio, thumbnail) -> transcribe || scenes -> analyze
-> find_clips -> render -> qc (auto-repair) -> metadata -> schedule/upload -> analytics -> learning
"""

from __future__ import annotations

import contextlib
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import delete, func, select

from shortforge.core.errors import DependencyMissing, ShortForgeError, SourceResolutionError
from shortforge.core.logging import get_logger
from shortforge.core.settings_store import set_value
from shortforge.database.models import (
    AnalyticsSnapshot,
    CandidateClip,
    Channel,
    ClipScore,
    Download,
    Face,
    LearningFeature,
    Render,
    Scene,
    Short,
    Source,
    TimelineSegment,
    Transcript,
    TranscriptWord,
    Upload,
    Video,
    VideoFile,
)
from shortforge.database.models import (
    EditTimeline as EditTimelineRow,
)
from shortforge.database.session import session_scope
from shortforge.engines.audio.analysis import analyze_audio, extract_audio
from shortforge.engines.captions.ass import HookOverlay, build_ass, write_ass
from shortforge.engines.captions.layout import CapWord, LayoutConfig
from shortforge.engines.captions.presets import fonts_dir, get_preset
from shortforge.engines.clip_detection.candidates import TimelineContext
from shortforge.engines.clip_detection.dedupe import ExistingClip
from shortforge.engines.clip_detection.features import DocumentStats, sentence_features
from shortforge.engines.clip_detection.pipeline import FinderConfig, FinderInputs, find_clips
from shortforge.engines.clip_detection.semantic import heuristic_timeline, llm_timeline
from shortforge.engines.editing.auto_editor import ClipSpec, build_timeline
from shortforge.engines.editing.timeline import EditTimeline
from shortforge.engines.media import ffmpeg as ff
from shortforge.engines.media.proxy import make_proxy, make_thumbnail
from shortforge.engines.metadata.generator import generate_metadata, pick_cover_frame
from shortforge.engines.quality_control.qc import apply_repairs, run_qc
from shortforge.engines.ranking.weights import METRIC_LABELS
from shortforge.engines.rendering.renderer import RenderOptions, render_timeline
from shortforge.engines.scenes.detect import detect_scenes
from shortforge.engines.transcription.whisper import WhisperTranscriber
from shortforge.engines.youtube.provider import VideoMeta
from shortforge.engines.youtube.urls import SourceKind, parse_source
from shortforge.workers.context import get_context
from shortforge.workers.helpers import (
    activity_path,
    audio_features_path,
    audio_wav_path,
    effective,
    latest_transcript,
    load_activity,
    load_audio_features,
    load_cuts,
    load_sentences,
    load_words,
    proxy_path,
    quick_hash,
    ranking_weights,
    render_path,
    source_for,
    utcnow,
)
from shortforge.workers.queue import JobContext, job_handler

log = get_logger("stages")

PRIORITY = {"scan": 60, "download": 55, "prepare": 58, "transcribe": 62, "scenes": 57, "analyze": 64,
            "find": 66, "render": 70, "qc": 72, "metadata": 74, "upload": 80}


def ensure_disk_space(folder: Path, min_gb: float) -> None:
    """Pause work (retry later) instead of failing midway when the data drive is nearly full."""
    try:
        free_gb = shutil.disk_usage(folder).free / 1024**3
    except OSError:
        return
    if free_gb < min_gb:
        from shortforge.core.errors import RetryableError

        get_context().notify("render_failed", "Low disk space",
                             f"Only {free_gb:.1f} GB free on the ShortForge drive. Free up space or change the storage "
                             "location; work will resume automatically.", level="warning")
        raise RetryableError(f"Low disk space ({free_gb:.1f} GB free, {min_gb:.0f} GB required).")


def _set_video(video_id: int, **fields: Any) -> None:
    with session_scope() as s:
        v = s.get(Video, video_id)
        if v is not None:
            for k, val in fields.items():
                setattr(v, k, val)
    get_context().bus.publish("video.updated", {"video_id": video_id, **{k: v for k, v in fields.items()
                                                                          if isinstance(v, (str, int, float, type(None)))}})


def _set_short(short_id: int, **fields: Any) -> None:
    with session_scope() as s:
        sh = s.get(Short, short_id)
        if sh is not None:
            for k, val in fields.items():
                setattr(sh, k, val)
    get_context().bus.publish("short.updated", {"short_id": short_id, **{k: v for k, v in fields.items()
                                                                         if isinstance(v, (str, int, float, type(None)))}})


# ============================================================================ discovery
def _upsert_channel(s, info) -> None:  # type: ignore[no-untyped-def]
    ch = s.get(Channel, info.id)
    if ch is None:
        ch = Channel(id=info.id)
        s.add(ch)
    ch.name = info.name or ch.name
    ch.handle = info.handle or ch.handle
    ch.url = info.url or ch.url
    ch.avatar_url = info.avatar_url or ch.avatar_url
    ch.banner_url = info.banner_url or ch.banner_url
    ch.description = info.description or ch.description
    if info.subscriber_count is not None:
        ch.subscriber_count = info.subscriber_count
    if info.video_count is not None:
        ch.video_count = info.video_count


def _eligible(meta: VideoMeta, src: Source, now: datetime) -> tuple[bool, str]:
    if meta.is_live_or_upcoming:
        return False, "live or upcoming"
    if src.kind == SourceKind.VIDEO:
        return True, ""  # a directly pasted video is explicit intent: no age/duration windows
    if meta.duration is not None:
        if src.min_duration_s and meta.duration < src.min_duration_s:
            return False, f"shorter than {src.min_duration_s:.0f}s"
        if src.max_duration_s and meta.duration > src.max_duration_s:
            return False, f"longer than {src.max_duration_s:.0f}s"
    if (src.max_video_age_days and meta.published_at is not None
            and meta.published_at < now - timedelta(days=src.max_video_age_days)):
        return False, f"older than {src.max_video_age_days} days"
    return True, ""


def _add_video(s, meta: VideoMeta, source_id: int | None) -> Video:  # type: ignore[no-untyped-def]
    v = Video(youtube_id=meta.id, source_id=source_id, channel_id=meta.channel_id, channel_name=meta.channel_name,
              title=meta.title, description=meta.description, thumbnail_url=meta.thumbnail_url,
              duration_s=meta.duration, published_at=meta.published_at, source_url=meta.url,
              view_count=meta.view_count, tags=meta.tags[:30] if meta.tags else None)
    if meta.channel_id and s.get(Channel, meta.channel_id) is None:
        s.add(Channel(id=meta.channel_id, name=meta.channel_name or ""))
    s.add(v)
    s.flush()
    return v


@job_handler("scan_source", resource="network", max_retries=3, priority=PRIORITY["scan"], label="Discover")
def scan_source(ctx: JobContext) -> dict[str, Any]:
    app = get_context()
    settings = app.settings()
    with session_scope() as s:
        src = s.get(Source, ctx.source_id)
        if src is None:
            raise ShortForgeError("Source no longer exists.")
        src.status = "scanning"
        src_input, kind, max_per_scan = src.input, src.kind, src.max_videos_per_scan
    app.bus.publish("source.updated", {"source_id": ctx.source_id, "status": "scanning"})
    ctx.progress(0.05, "Resolving source")
    try:
        ref = parse_source(src_input)
        if kind == SourceKind.LOCAL_FILE:
            with session_scope() as s:
                exists = s.execute(select(Video.id).where(Video.local_path == ref.value)).scalar()
                if not exists:
                    v = Video(source_id=ctx.source_id, title=Path(ref.value).stem, local_path=ref.value,
                              source_url=ref.value, download_status="done")
                    s.add(v)
                    s.flush()
                    ctx.enqueue("import_local", video_id=v.id, source_id=ctx.source_id,
                                dedupe_key=f"import:{v.id}")
            return {"new": 0 if exists else 1}
        provider = app.source_provider(settings)
        with session_scope() as s:
            known = set(s.execute(select(Video.youtube_id).where(Video.youtube_id.is_not(None))).scalars().all())
        ctx.progress(0.2, "Listing videos")
        listing_limit = max(15, max_per_scan * 4)
        if ref.is_channel_like:
            channel, videos, count = provider._channel_and_uploads(ref, listing_limit)
            with session_scope() as s:
                _upsert_channel(s, channel)
                if count and not channel.video_count:
                    s.get(Channel, channel.id).video_count = count
                src = s.get(Source, ctx.source_id)
                src.channel_id = channel.id
                src.title = channel.name
                src.thumbnail_url = channel.avatar_url
        else:
            videos = provider.list_videos(ref, listing_limit)
        now = utcnow()
        new_ids: list[int] = []
        queued = 0
        skipped: dict[str, int] = {}
        with session_scope() as s:
            max_age = s.get(Source, ctx.source_id).max_video_age_days
        # Network work first (never hold a DB write transaction during I/O).
        fresh = [m for m in videos if m.id and m.id not in known]
        for i, meta in enumerate(fresh):
            if meta.duration is None or (max_age and meta.published_at is None):
                ctx.progress(0.3 + 0.6 * i / max(1, len(fresh)), f"Reading metadata ({i + 1}/{len(fresh)})")
                try:
                    full = provider.get_video_metadata(meta.id)
                    meta.duration = meta.duration or full.duration
                    meta.published_at = meta.published_at or full.published_at
                    meta.live_status = full.live_status
                    meta.description = meta.description or full.description
                    meta.channel_id = meta.channel_id or full.channel_id
                    meta.channel_name = meta.channel_name or full.channel_name
                except (SourceResolutionError, ShortForgeError) as exc:
                    ctx.log(f"metadata for {meta.id} unavailable: {exc}")
        with session_scope() as s:
            src = s.get(Source, ctx.source_id)
            first_scan = src.last_scan_at is None
            if kind == SourceKind.VIDEO and fresh:
                src.title = fresh[0].title
                src.thumbnail_url = fresh[0].thumbnail_url
            for meta in fresh:
                v = _add_video(s, meta, ctx.source_id)
                new_ids.append(v.id)
                ok, why = _eligible(meta, src, now)
                allowed_existing = src.process_existing or not first_scan or kind == SourceKind.VIDEO
                if ok and allowed_existing and src.auto_download and queued < max(1, src.max_videos_per_scan):
                    v.download_status = "queued"
                    v.processing_status = "queued"
                    queued += 1
                    ctx.enqueue("download_video", video_id=v.id, source_id=src.id, priority=PRIORITY["download"]
                                + min(20, src.priority // 5), dedupe_key=f"download:{v.id}")
                else:
                    v.processing_status = "ignored" if not ok else "new"
                    v.error = f"Skipped: {why}" if not ok else None
                    skipped[why or "not selected"] = skipped.get(why or "not selected", 0) + 1
            interval = src.scan_interval_min or settings.autopilot.scan_interval_min
            src.last_scan_at = now
            src.next_scan_at = now + timedelta(minutes=interval)
            src.status = "idle"
            src.last_error = None
        ctx.log(f"discovered {len(new_ids)} new video(s), queued {queued}, skipped {skipped}")
        app.bus.publish("source.updated", {"source_id": ctx.source_id, "status": "idle"})
        return {"discovered": len(new_ids), "queued": queued, "skipped": skipped}
    except Exception as exc:
        with session_scope() as s:
            src = s.get(Source, ctx.source_id)
            if src is not None:
                src.status = "error"
                src.last_error = getattr(exc, "message", str(exc))[:500]
                src.next_scan_at = utcnow() + timedelta(minutes=30)
        app.bus.publish("source.updated", {"source_id": ctx.source_id, "status": "error"})
        raise


# ============================================================================ acquisition
@job_handler("download_video", resource="network", max_retries=4, priority=PRIORITY["download"], label="Download")
def download_video(ctx: JobContext) -> dict[str, Any]:
    app = get_context()
    settings = app.settings()
    with session_scope() as s:
        v = s.get(Video, ctx.video_id)
        if v is None:
            raise ShortForgeError("Video no longer exists.")
        meta = VideoMeta(id=v.youtube_id or "", title=v.title, url=v.source_url or "", duration=v.duration_s)
        folder = app.paths.sources / (v.channel_id or "misc")
        dl = Download(video_id=v.id, status="downloading", quality=settings.youtube.download_quality,
                      started_at=utcnow(), attempts=ctx.attempt + 1)
        s.add(dl)
        s.flush()
        dl_id = dl.id
        v.download_status = "downloading"
        v.stage = "download"
    ensure_disk_space(app.paths.sources, settings.min_free_disk_gb)
    provider = app.source_provider(settings)

    def on_progress(p) -> None:  # type: ignore[no-untyped-def]
        label = {"downloading": "Downloading", "merging": "Merging audio/video", "verifying": "Verifying"}[p.phase]
        speed = f" · {p.speed_bps / 1e6:.1f} MB/s" if p.speed_bps else ""
        ctx.progress(p.fraction * 0.97 if p.phase == "downloading" else 0.98, f"{label}{speed}")

    try:
        result = provider.download_video(meta, folder, quality=settings.youtube.download_quality,
                                         progress=on_progress, cancel=ctx.cancel)
    except Exception as exc:
        with session_scope() as s:
            dl = s.get(Download, dl_id)
            dl.status, dl.error, dl.finished_at = "failed", getattr(exc, "message", str(exc))[:500], utcnow()
            v = s.get(Video, ctx.video_id)
            v.download_status = "failed" if not getattr(exc, "retryable", False) else "queued"
            v.error = getattr(exc, "message", str(exc))[:500]
        raise
    with session_scope() as s:
        dl = s.get(Download, dl_id)
        dl.status, dl.finished_at, dl.path = "done", utcnow(), str(result.path)
        dl.bytes_total = dl.bytes_done = result.size
        dl.format_id = result.format_id
        v = s.get(Video, ctx.video_id)
        v.local_path = str(result.path)
        v.width, v.height, v.fps = result.width, result.height, result.fps
        v.file_size = result.size
        v.duration_s = result.duration or v.duration_s
        v.download_status = "done"
        v.error = None
        v.language = v.language or result.audio_language
        s.add(VideoFile(video_id=v.id, kind="source", path=str(result.path), size=result.size, width=result.width,
                        height=result.height, codec=result.vcodec))
        title = v.title
    app.notify("download_complete", "Download complete", title, link=f"/videos/{ctx.video_id}")
    ctx.enqueue("prepare_media", video_id=ctx.video_id, source_id=ctx.source_id, priority=PRIORITY["prepare"],
                dedupe_key=f"prepare:{ctx.video_id}")
    return {"path": str(result.path), "format": result.format_id, "audio_language": result.audio_language}


@job_handler("import_local", resource="io", max_retries=1, priority=PRIORITY["download"], label="Import")
def import_local(ctx: JobContext) -> dict[str, Any]:
    with session_scope() as s:
        v = s.get(Video, ctx.video_id)
        path = Path(v.local_path or "")
    if not path.exists():
        raise ShortForgeError(f"File not found: {path}")
    info = ff.probe(path)
    if not info.has_video:
        raise ShortForgeError("The file has no video stream.")
    digest = quick_hash(path)
    w, h = info.display_size
    with session_scope() as s:
        dup = s.execute(select(Video.id).where(Video.content_hash == digest, Video.id != ctx.video_id)).scalar()
        v = s.get(Video, ctx.video_id)
        v.content_hash = digest
        v.width, v.height, v.fps, v.duration_s, v.file_size = w, h, info.fps, info.duration, info.size
        v.download_status = "done"
        s.add(VideoFile(video_id=v.id, kind="source", path=str(path), size=info.size, width=w, height=h,
                        codec=info.vcodec))
        if dup:
            v.processing_status = "ignored"
            v.error = f"Same file as video #{dup}"
            return {"duplicate_of": dup}
    ctx.enqueue("prepare_media", video_id=ctx.video_id, priority=PRIORITY["prepare"], dedupe_key=f"prepare:{ctx.video_id}")
    return {"duration": info.duration}


@job_handler("prepare_media", resource="cpu", max_retries=2, priority=PRIORITY["prepare"], label="Proxy")
def prepare_media(ctx: JobContext) -> dict[str, Any]:
    app = get_context()
    with session_scope() as s:
        v = s.get(Video, ctx.video_id)
        src = Path(v.local_path or "")
        duration = v.duration_s
        v.processing_status, v.stage = "processing", "prepare"
    if not src.exists():
        raise ShortForgeError("Source media is missing; re-download the video.")
    ppath = proxy_path(app.paths, ctx.video_id)
    wav = audio_wav_path(app.paths, ctx.video_id)
    thumb = app.paths.thumbnails / f"video_{ctx.video_id}.jpg"
    if not wav.exists():
        extract_audio(src, wav, progress=ctx.sub(0.0, 0.15), cancel=ctx.cancel, duration=duration)
    if not ppath.exists():
        make_proxy(src, ppath, duration=duration, progress=ctx.sub(0.15, 0.95), cancel=ctx.cancel)
    if not thumb.exists():
        try:
            make_thumbnail(ppath, thumb, at=min(30.0, (duration or 60) * 0.1))
        except ff.FFmpegError as exc:
            ctx.log(f"thumbnail failed: {exc.message}", "warning")
    with session_scope() as s:
        v = s.get(Video, ctx.video_id)
        v.proxy_path, v.audio_path = str(ppath), str(wav)
        if thumb.exists():
            v.thumbnail_path = str(thumb)
        s.execute(delete(VideoFile).where(VideoFile.video_id == v.id, VideoFile.kind.in_(("proxy", "audio"))))
        s.add(VideoFile(video_id=v.id, kind="proxy", path=str(ppath), size=ppath.stat().st_size))
        s.add(VideoFile(video_id=v.id, kind="audio", path=str(wav), size=wav.stat().st_size))
    ctx.enqueue("transcribe", video_id=ctx.video_id, priority=PRIORITY["transcribe"], dedupe_key=f"transcribe:{ctx.video_id}")
    ctx.enqueue("detect_scenes", video_id=ctx.video_id, priority=PRIORITY["scenes"], dedupe_key=f"scenes:{ctx.video_id}")
    return {"proxy": str(ppath)}


# ============================================================================ analysis
def _maybe_enqueue_analysis(ctx: JobContext) -> None:
    with session_scope() as s:
        v = s.get(Video, ctx.video_id)
        ready = v.transcript_status == "done" and v.scenes_status == "done"
    if ready:
        ctx.enqueue("analyze_video", video_id=ctx.video_id, priority=PRIORITY["analyze"],
                    dedupe_key=f"analyze:{ctx.video_id}")


@job_handler("transcribe", resource="gpu", max_retries=2, priority=PRIORITY["transcribe"], label="Transcribe")
def transcribe(ctx: JobContext) -> dict[str, Any]:
    app = get_context()
    settings = app.settings()
    with session_scope() as s:
        v = s.get(Video, ctx.video_id)
        existing = latest_transcript(s, ctx.video_id)
        if existing is not None and not ctx.payload.get("force"):
            v.transcript_status = "done"
            cached = True
        else:
            cached = False
            v.transcript_status, v.stage = "running", "transcribe"
        duration = v.duration_s
    if cached:
        ctx.log("transcript cached; skipping transcription")
        _maybe_enqueue_analysis(ctx)
        return {"cached": True}
    hw = app.hardware()
    tr = WhisperTranscriber(app.paths.models, settings.transcription, recommended=hw.recommended_whisper_model,
                            cuda_available=hw.cuda_available, allow_cpu_fallback=settings.gpu.allow_cpu_fallback)
    try:
        result = tr.transcribe(audio_wav_path(app.paths, ctx.video_id), duration=duration,
                               progress=ctx.sub(0.0, 0.97), cancel=ctx.cancel)
    except DependencyMissing:
        _set_video(ctx.video_id, transcript_status="failed")
        raise
    if not result.words:
        _set_video(ctx.video_id, transcript_status="failed", error="No speech was detected in this video.")
        raise ShortForgeError("No speech was detected in this video, so no clips can be found.")
    with session_scope() as s:
        if ctx.payload.get("force"):
            s.execute(delete(Transcript).where(Transcript.video_id == ctx.video_id))
        t = Transcript(video_id=ctx.video_id, model=result.model, device=result.device,
                       compute_type=result.compute_type, language=result.language,
                       language_probability=result.language_probability, duration=result.duration, text=result.text,
                       segments=[seg.to_dict() for seg in result.segments],
                       sentences=[x.to_dict() for x in result.sentences], elapsed_s=result.elapsed_s)
        s.add(t)
        s.flush()
        s.bulk_insert_mappings(TranscriptWord, [
            {"transcript_id": t.id, "idx": w.idx, "word": w.text[:200], "start": w.start, "end": w.end,
             "probability": w.prob, "segment_idx": w.segment_idx, "sentence_idx": w.sentence_idx}
            for w in result.words])
        v = s.get(Video, ctx.video_id)
        v.transcript_status = "done"
        v.language = result.language
    _maybe_enqueue_analysis(ctx)
    return {"words": len(result.words), "language": result.language, "device": result.device,
            "model": result.model, "elapsed_s": result.elapsed_s}


@job_handler("detect_scenes", resource="cpu", max_retries=2, priority=PRIORITY["scenes"], label="Scenes")
def detect_scenes_job(ctx: JobContext) -> dict[str, Any]:
    app = get_context()
    with session_scope() as s:
        v = s.get(Video, ctx.video_id)
        ppath = Path(v.proxy_path or "")
        v.scenes_status = "running"
    result = detect_scenes(ppath, progress=ctx.sub(0.0, 0.97), cancel=ctx.cancel)
    np.save(activity_path(app.paths, ctx.video_id), result.activity)
    with session_scope() as s:
        s.execute(delete(Scene).where(Scene.video_id == ctx.video_id))
        s.bulk_insert_mappings(Scene, [{"video_id": ctx.video_id, "idx": i, "start": a, "end": b, "kind": "cut"}
                                       for i, (a, b) in enumerate(result.scenes)])
        s.get(Video, ctx.video_id).scenes_status = "done"
    _maybe_enqueue_analysis(ctx)
    return {"scenes": len(result.scenes)}


@job_handler("analyze_video", resource="gpu", max_retries=2, priority=PRIORITY["analyze"], label="Analyze")
def analyze_video(ctx: JobContext) -> dict[str, Any]:
    app = get_context()
    settings = app.settings()
    with session_scope() as s:
        v = s.get(Video, ctx.video_id)
        v.analysis_status, v.stage = "running", "analyze"
        title = v.title
        t = latest_transcript(s, ctx.video_id)
        if t is None:
            raise ShortForgeError("Transcript missing; transcribe first.")
        words = load_words(s, t)
        sentences = load_sentences(t)
        duration = v.duration_s or t.duration or 0.0
    ctx.progress(0.03, "Analysing audio (speech, energy, silence)")
    features = analyze_audio(audio_wav_path(app.paths, ctx.video_id))
    features.to_npz(audio_features_path(app.paths, ctx.video_id))
    feats = sentence_features(sentences, words)
    llm = app.llm(settings)
    source = "heuristic"
    if llm is not None:
        from shortforge.core.gpu import models as mm

        ctx.progress(0.1, f"Understanding content with {llm.model}")
        with mm.gpu_session("llm"):
            mm.load(f"llm:{llm.model}", lambda: llm, vram_mb=5500, unload=lambda p: p.unload())
            segments = llm_timeline(llm, sentences, title, progress=ctx.sub(0.1, 0.97), feats=feats,
                                    system=settings.prompts.timeline or None)
            if settings.llm.unload_after_use:
                mm.unload(f"llm:{llm.model}")
        source = "llm"
    else:
        ctx.log("no local LLM available; using heuristic content timeline")
        segments = heuristic_timeline(sentences, feats, duration)
    with session_scope() as s:
        s.execute(delete(TimelineSegment).where(TimelineSegment.video_id == ctx.video_id))
        for seg in segments:
            s.add(TimelineSegment(video_id=ctx.video_id, start=seg.start, end=seg.end, label=seg.label,
                                  summary=seg.summary, interest=seg.interest, source=seg.source,
                                  energy=round(features.energy_at(seg.start, seg.end), 2)))
        v = s.get(Video, ctx.video_id)
        v.analysis_status = "analyzed"
        v.analysis = {**(v.analysis or {}), "audio": features.summary(), "timeline_source": source,
                      "llm_model": llm.model if llm else None}
    app.notify("video_analyzed", "Video analysed", title, link=f"/videos/{ctx.video_id}")
    ctx.enqueue("find_clips", video_id=ctx.video_id, priority=PRIORITY["find"], dedupe_key=f"find:{ctx.video_id}")
    return {"segments": len(segments), "timeline_source": source}


def _existing_clips(s, video_id: int) -> list[ExistingClip]:  # type: ignore[no-untyped-def]
    out: list[ExistingClip] = []
    for c in s.execute(select(CandidateClip).where(CandidateClip.video_id == video_id,
                                                   CandidateClip.status.in_(("generated", "favorite", "selected")))).scalars():
        out.append(ExistingClip(f"candidate:{c.id}", c.video_id, c.start, c.end, c.text))
    rows = s.execute(select(Short.id, Short.video_id, Short.start, Short.end, CandidateClip.text)
                     .join(CandidateClip, CandidateClip.id == Short.candidate_id, isouter=True)
                     .order_by(Short.id.desc()).limit(400)).all()
    for sid, vid, st, en, text in rows:
        out.append(ExistingClip(f"short:{sid}", vid, st, en, text or ""))
    return out


@job_handler("find_clips", resource="gpu", max_retries=2, priority=PRIORITY["find"], label="Find clips")
def find_clips_job(ctx: JobContext) -> dict[str, Any]:
    app = get_context()
    settings = app.settings()
    with session_scope() as s:
        v = s.get(Video, ctx.video_id)
        v.stage = "find_clips"
        src = source_for(s, v)
        eff = effective(settings, src)
        t = latest_transcript(s, ctx.video_id)
        words, sentences = load_words(s, t), load_sentences(t)
        cuts = load_cuts(s, ctx.video_id)
        timeline = TimelineContext([(x.start, x.end, x.label, x.interest or 45.0) for x in
                                    s.execute(select(TimelineSegment).where(TimelineSegment.video_id == ctx.video_id)).scalars()])
        existing = _existing_clips(s, ctx.video_id)
        if ctx.payload.get("append"):
            existing += [ExistingClip(f"candidate:{c.id}", c.video_id, c.start, c.end, c.text) for c in
                         s.execute(select(CandidateClip).where(CandidateClip.video_id == ctx.video_id)).scalars()]
        weights = ranking_weights(s)
        title, channel_name, duration = v.title, v.channel_name, v.duration_s or t.duration
        ppath = Path(v.proxy_path) if v.proxy_path else None
    inputs = FinderInputs(ctx.video_id, title, channel_name, duration, words, sentences,
                          load_audio_features(app.paths, ctx.video_id), cuts, load_activity(app.paths, ctx.video_id),
                          timeline, ppath, existing)
    cfg = FinderConfig(min_duration=eff["min_duration"], max_duration=eff["max_duration"],
                       target_duration=settings.clips.target_duration, llm_candidates=settings.llm.max_candidates,
                       duplicate_threshold=settings.clips.duplicate_threshold, weights=weights,
                       ranker_prompt=settings.prompts.clip_ranker or None)
    llm = app.llm(settings)
    from shortforge.core.gpu import models as mm

    def embed(texts: list[str]) -> list[list[float]] | None:
        return llm.embed(texts) if llm is not None else None

    with mm.gpu_session("clip-finder"):
        if llm is not None:
            mm.load(f"llm:{llm.model}", lambda: llm, vram_mb=5500, unload=lambda p: p.unload())
        try:
            result = find_clips(inputs, cfg, llm=llm, face_detector=app.face_detector(), progress=ctx.sub(0, 0.95),
                                cancel=ctx.cancel, embed=embed if llm else None)
        finally:
            if llm is not None and settings.llm.unload_after_use:
                mm.unload(f"llm:{llm.model}")
    with session_scope() as s:
        if not ctx.payload.get("append"):
            old = s.execute(select(CandidateClip.id).where(CandidateClip.video_id == ctx.video_id,
                                                           CandidateClip.status.in_(("candidate", "rejected")),
                                                           CandidateClip.favorite.is_(False))).scalars().all()
            if old:
                s.execute(delete(CandidateClip).where(CandidateClip.id.in_(old)))
        s.execute(delete(Face).where(Face.video_id == ctx.video_id))
        created: list[int] = []
        for rank, c in enumerate(result.candidates, start=1):
            llm_data = c.llm or {}
            vision = dict(c.vision or {})
            detections = vision.pop("detections", [])
            row = CandidateClip(
                video_id=ctx.video_id, start=c.start, end=c.end, duration=round(c.end - c.start, 3),
                first_word_idx=c.first_word, last_word_idx=c.last_word, text=c.text,
                title=llm_data.get("title") or None, hook_text=llm_data.get("hook_text"),
                reasoning=llm_data.get("reason") or "; ".join(c.notes) or None,
                labels={"hook_type": llm_data.get("hook_type"), "category": llm_data.get("category"),
                        "topic": llm_data.get("topic")},
                keywords=llm_data.get("keywords") or [], analysis={"vision": vision, "notes": c.notes},
                heuristic_score=c.heuristic, llm_score=round(sum(llm_data.get(m, 0) for m in
                                                                ("hook", "standalone", "payoff", "story_completeness"))
                                                            / 4, 2) if c.llm else None,
                score=c.final, rank=rank, duplicate_of=c.duplicate_of, similarity=c.similarity,
                pass_reached=c.pass_reached, llm_model=llm_data.get("model"),
                status="duplicate" if c.duplicate_of else "candidate",
            )
            s.add(row)
            s.flush()
            created.append(row.id)
            bd = c.breakdown
            if bd:
                for metric, value in bd.metrics.items():
                    s.add(ClipScore(candidate_id=row.id, metric=metric, value=round(value, 2),
                                    weight=weights.get(metric, 0.0), kind="score",
                                    source="llm" if c.llm and metric in llm_data else "heuristic"))
                for metric, value in bd.penalties.items():
                    s.add(ClipScore(candidate_id=row.id, metric=metric, value=round(-value, 2), kind="penalty"))
            for d in detections[:400]:
                s.add(Face(video_id=ctx.video_id, t=d["t"], x=d["x"], y=d["y"], w=d["w"], h=d["h"],
                           score=d["score"], track_id=d["track_id"]))
        v = s.get(Video, ctx.video_id)
        v.processing_status = "analyzed"
        v.stage = None
        v.analysis = {**(v.analysis or {}), "passes": result.passes, "spans_considered": result.considered,
                      "llm_model": result.llm_used, "vision_backend": result.vision_backend}
    app.bus.publish("candidates.updated", {"video_id": ctx.video_id})
    generated = _auto_generate(ctx, created) if not ctx.payload.get("no_generate") else []
    return {"candidates": len(created), "llm": result.llm_used, "generated_shorts": generated}


def _auto_generate(ctx: JobContext, candidate_ids: list[int]) -> list[int]:
    app = get_context()
    settings = app.settings()
    if not (settings.general.auto_generate_shorts or settings.autopilot.enabled):
        return []
    with session_scope() as s:
        v = s.get(Video, ctx.video_id)
        src = source_for(s, v)
        eff = effective(settings, src)
        cands = s.execute(select(CandidateClip).where(CandidateClip.id.in_(candidate_ids),
                                                      CandidateClip.duplicate_of.is_(None))
                          .order_by(CandidateClip.score.desc())).scalars().all()
        picked = [c for c in cands if c.score >= eff["min_score"]][: eff["max_shorts"]]
        best_available = False
        if not picked and cands and not settings.autopilot.enabled:
            picked = cands[:1]  # manual mode: always show the user at least the strongest moment
            best_available = True
        if eff["max_shorts_per_day"] and src is not None:
            today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            made_today = s.execute(select(func.count(Short.id)).join(Video, Video.id == Short.video_id)
                                   .where(Video.source_id == src.id, Short.created_at >= today)).scalar() or 0
            picked = picked[: max(0, eff["max_shorts_per_day"] - made_today)]
        ids = []
        for c in picked:
            ids.append(create_short(s, c, eff["caption_preset"], eff["render_profile"], eff["reframe_mode"]))
            if best_available:
                s.get(Short, ids[-1]).error = "Below the minimum score; generated as the best available moment."
    for sid in ids:
        ctx.enqueue("render_short", short_id=sid, video_id=ctx.video_id, priority=PRIORITY["render"],
                    dedupe_key=f"render:{sid}")
    return ids


def create_short(s, c: CandidateClip, preset: str, profile: str, reframe_mode: str) -> int:  # type: ignore[no-untyped-def]
    sh = Short(candidate_id=c.id, video_id=c.video_id, title=c.title or (c.text[:60] + "…"), status="draft",
               caption_preset=preset, render_profile=profile, reframe_mode=reframe_mode, start=c.start, end=c.end,
               duration=c.duration, score=c.score)
    c.status = "generated"
    s.add(sh)
    s.flush()
    return sh.id


# ============================================================================ editing + rendering
def current_timeline(s, short_id: int) -> EditTimelineRow | None:  # type: ignore[no-untyped-def]
    return s.execute(select(EditTimelineRow).where(EditTimelineRow.short_id == short_id)
                     .order_by(EditTimelineRow.version.desc()).limit(1)).scalar()


def _build_auto_timeline(short_id: int, *, reframe_override: dict | None = None) -> tuple[EditTimeline, dict]:
    app = get_context()
    settings = app.settings()
    with session_scope() as s:
        sh = s.get(Short, short_id)
        v = s.get(Video, sh.video_id)
        c = s.get(CandidateClip, sh.candidate_id) if sh.candidate_id else None
        t = latest_transcript(s, v.id)
        words = load_words(s, t)
        sentences = load_sentences(t)
        cuts = load_cuts(s, v.id)
        src_path, ppath = v.local_path, v.proxy_path
        width, height, fps = v.width, v.height, v.fps
        keywords = (c.keywords or []) if c else []
        hook_text = c.hook_text if c else None
        preset, rmode = sh.caption_preset, sh.reframe_mode
        start, end = sh.start, sh.end
    if not src_path or not Path(src_path).exists():
        raise ShortForgeError("Source media is missing; re-download or re-import the video.")
    if not width or not height:
        info = ff.probe(src_path)
        (width, height), fps = info.display_size, info.fps
    audio = load_audio_features(app.paths, sh.video_id)
    feats = sentence_features(sentences, words)
    doc = DocumentStats(feats)
    idf = {tok: doc.idf(tok) for tok in doc.df}
    if reframe_override:
        for k, val in reframe_override.items():
            setattr(settings.reframe, k, val)
    spec = ClipSpec(source_path=src_path, analysis_video=ppath or src_path, source_width=width, source_height=height,
                    source_fps=fps or 30.0, start=start, end=end, words=words, scene_cuts=cuts,
                    speech=audio.speech if audio else [], keywords=keywords, hook_text=hook_text, idf=idf,
                    caption_preset=preset, reframe_mode=rmode,
                    broll_clips=_broll_library(settings), music_tracks=_music_library(settings))
    return build_timeline(spec, settings, app.face_detector())


def _broll_library(settings) -> list:  # type: ignore[no-untyped-def]
    if not settings.broll.enabled or not settings.broll.library_dir or not Path(settings.broll.library_dir).is_dir():
        return []
    from shortforge.engines.editing.broll import index_library, ollama_vision_captioner
    from shortforge.engines.llm.providers import OllamaProvider

    captioner = None
    if settings.llm.provider == "ollama":
        installed = OllamaProvider("", settings.llm.ollama_url).list_models()
        captioner = ollama_vision_captioner(settings.llm.ollama_url, installed)
    return index_library(Path(settings.broll.library_dir), get_context().paths.cache / "broll_index.json", captioner)


def _music_library(settings) -> list:  # type: ignore[no-untyped-def]
    if not settings.music.enabled or not settings.music.library_dir or not Path(settings.music.library_dir).is_dir():
        return []
    from shortforge.engines.audio.music import index_library

    return index_library(Path(settings.music.library_dir), get_context().paths.cache / "music_index.json")


def caption_preset_for(tl: EditTimeline):  # type: ignore[no-untyped-def]
    preset = get_preset(tl.captions.preset).model_copy(deep=True)
    ov = tl.captions.overrides or {}
    for key, val in ov.items():
        if key == "font_scale":
            preset.font_size = max(24, round(preset.font_size * float(val)))
        elif hasattr(preset, key):
            setattr(preset, key, val)
    return preset


def build_captions(tl: EditTimeline, out: Path):  # type: ignore[no-untyped-def]
    preset = caption_preset_for(tl)
    cfg = LayoutConfig(tl.width, tl.height)
    cfg.safe.top, cfg.safe.bottom, cfg.safe.left, cfg.safe.right = (tl.safe_area.top, tl.safe_area.bottom,
                                                                    tl.safe_area.left, tl.safe_area.right)
    words = [CapWord(w.text, w.start, w.end, w.emphasis, w.speaker) for w in tl.captions.words] if tl.captions.enabled else []
    hook = next((HookOverlay(o.text, o.start, o.end) for o in tl.overlays if o.kind == "hook"), None)
    content, pages = build_ass(words, preset, cfg, clip_duration=tl.duration, hook=hook,
                               max_words=tl.captions.max_words, vertical_position=tl.captions.vertical_position)
    write_ass(out, content)
    return pages


@job_handler("render_short", resource="render", max_retries=2, priority=PRIORITY["render"], label="Render")
def render_short(ctx: JobContext) -> dict[str, Any]:
    app = get_context()
    settings = app.settings()
    _set_short(ctx.short_id, status="rendering", error=None)
    with session_scope() as s:
        sh = s.get(Short, ctx.short_id)
        if sh is None:
            raise ShortForgeError("Short no longer exists.")
        row = current_timeline(s, ctx.short_id)
        profile = sh.render_profile or settings.general.render_profile
        video_id = sh.video_id
        tl_data = row.data if row is not None and not ctx.payload.get("rebuild") else None
        version = (row.version + 1) if row is not None else 1
    report: dict[str, Any] = {}
    ensure_disk_space(app.paths.renders, settings.min_free_disk_gb)
    if tl_data is None:
        ctx.progress(0.02, "Planning edit (reframing, captions, pacing)")
        override = {"deadzone": 0.03, "min_hold_s": 0.8} if ctx.payload.get("replan") else None
        tl, report = _build_auto_timeline(ctx.short_id, reframe_override=override)
        with session_scope() as s:
            s.add(EditTimelineRow(short_id=ctx.short_id, version=version, data=tl.model_dump(),
                                  origin="repair" if ctx.payload.get("replan") else "auto"))
    else:
        tl = EditTimeline.model_validate(tl_data)
        version -= 1
        if "repair:replan_reframe" in tl.notes or "repair:smooth_camera" in tl.notes:
            fresh, report = _build_auto_timeline(ctx.short_id, reframe_override={"deadzone": 0.03, "min_hold_s": 1.6})
            tl.layouts, tl.crop = fresh.layouts, fresh.crop
            tl.notes = [n for n in tl.notes if not n.startswith("repair:")]
    ctx.progress(0.12, "Building animated captions")
    work = app.paths.temp / f"render_short_{ctx.short_id}"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    ass_path = app.paths.captions / f"short_{ctx.short_id}.ass"
    pages = build_captions(tl, ass_path)
    out = render_path(app.paths, video_id, ctx.short_id, version)
    rs = settings.render
    with session_scope() as s:
        r = Render(short_id=ctx.short_id, timeline_version=version, status="running", profile=profile,
                   started_at=utcnow())
        s.add(r)
        s.flush()
        render_id = r.id
    try:
        result = render_timeline(tl, out, work, options=RenderOptions(profile=profile, codec=rs.codec, encoder=rs.encoder,
                                                                     cq=rs.cq, bitrate_kbps=rs.bitrate_kbps,
                                                                     audio_bitrate_kbps=rs.audio_bitrate_kbps),
                                 ass_path=ass_path, fonts_dir=fonts_dir(), progress=ctx.sub(0.14, 0.99),
                                 cancel=ctx.cancel)
    except Exception as exc:
        with session_scope() as s:
            r = s.get(Render, render_id)
            r.status, r.error, r.finished_at = "failed", getattr(exc, "message", str(exc))[:500], utcnow()
        _set_short(ctx.short_id, status="failed", error=getattr(exc, "message", str(exc))[:500])
        if not getattr(exc, "retryable", False):
            app.notify("render_failed", "Render failed", getattr(exc, "message", str(exc))[:200], level="error",
                       link=f"/shorts/{ctx.short_id}")
        raise
    with session_scope() as s:
        r = s.get(Render, render_id)
        r.status, r.path, r.encoder = "done", str(result.path), result.encoder
        r.file_size, r.elapsed_s, r.finished_at = result.size, result.elapsed_s, utcnow()
        sh = s.get(Short, ctx.short_id)
        previous = sh.output_path
        sh.output_path = str(result.path)
        sh.duration = round(result.duration, 3)
        sh.width, sh.height = tl.width, tl.height
        sh.status = "review"
        data = dict(sh.metadata_options or {})
        data["render_report"] = report or data.get("render_report")
        data["crop_path"] = result.crop_path[:2000]
        sh.metadata_options = data
    if previous and previous != str(result.path) and Path(previous).exists():
        Path(previous).unlink(missing_ok=True)
    shutil.rmtree(work, ignore_errors=True)
    ctx.enqueue("qc_short", short_id=ctx.short_id, video_id=video_id, priority=PRIORITY["qc"],
                dedupe_key=f"qc:{ctx.short_id}", payload={"pages": len(pages)})
    return {"path": str(result.path), "encoder": result.encoder, "elapsed_s": result.elapsed_s,
            "fps": round(result.frames / max(0.01, result.elapsed_s), 1)}


@job_handler("qc_short", resource="cpu", max_retries=1, priority=PRIORITY["qc"], label="Quality check")
def qc_short(ctx: JobContext) -> dict[str, Any]:
    app = get_context()
    with session_scope() as s:
        sh = s.get(Short, ctx.short_id)
        row = current_timeline(s, ctx.short_id)
        tl = EditTimeline.model_validate(row.data)
        out = Path(sh.output_path or "")
        crop_path = [tuple(x) for x in (sh.metadata_options or {}).get("crop_path", [])]
        attempts = sh.repair_attempts
        t = latest_transcript(s, sh.video_id)
        words = load_words(s, t) if t else []
        others = {x.id: x.fingerprint.get("dhash") for x in s.execute(
            select(Short).where(Short.id != ctx.short_id, Short.fingerprint.is_not(None))
            .order_by(Short.id.desc()).limit(300)).scalars() if x.fingerprint and x.fingerprint.get("dhash")}
        faces = s.execute(select(Face).where(Face.video_id == sh.video_id, Face.t >= sh.start, Face.t <= sh.end)).scalars().all()
    ctx.progress(0.05, "Checking output")
    pages = build_captions(tl, app.paths.temp / f"qc_{ctx.short_id}.ass")
    clip_words = [(w.text, w.prob, w.start) for w in words if tl.ranges[0].start <= w.start <= tl.ranges[-1].end]
    speech_out = []
    audio = load_audio_features(app.paths, sh.video_id)
    if audio:
        for a, b in audio.speech:
            oa, ob = tl.source_to_output(max(a, tl.ranges[0].start)), tl.source_to_output(min(b, tl.ranges[-1].end))
            if oa is not None and ob is not None and ob > oa:
                speech_out.append((oa, ob))
    # Face track (largest face per sample, source px) for face-cropping check on crop layouts only.
    face_track = []
    by_t: dict[float, Face] = {}
    for f in faces:
        if f.t not in by_t or f.w > by_t[f.t].w:
            by_t[f.t] = f
    for tt, f in sorted(by_t.items()):
        o = tl.source_to_output(tt)
        seg = next((x for x in tl.layouts if o is not None and x.start <= o < x.end), None)
        if o is not None and seg is not None and seg.layout == "crop":
            face_track.append((o, f.x * tl.source_width, f.y * tl.source_height, f.w * tl.source_width,
                               f.h * tl.source_height))
    report, fingerprint = run_qc(out, tl, pages, crop_path=crop_path, words_prob=clip_words, speech_out=speech_out,
                                 existing_fingerprints=others, face_track=face_track)
    (app.paths.temp / f"qc_{ctx.short_id}.ass").unlink(missing_ok=True)
    repairs = [r for r in report.repairs if r]
    fixable = [i for i in report.issues if i.repair and (i.level == "FAIL" or i.repair in ("lower_gain", "shrink_captions"))]
    if fixable and attempts < 2:
        new_tl, notes = apply_repairs(tl, sorted({i.repair for i in fixable}))
        with session_scope() as s:
            sh = s.get(Short, ctx.short_id)
            sh.repair_attempts = attempts + 1
            sh.qc_status, sh.qc_report = report.status, {**report.to_dict(), "repairs": notes}
            row = current_timeline(s, ctx.short_id)
            s.add(EditTimelineRow(short_id=ctx.short_id, version=row.version + 1, data=new_tl.model_dump(),
                                  origin="repair"))
        ctx.log(f"auto-repair attempt {attempts + 1}: {', '.join(notes)}")
        ctx.enqueue("render_short", short_id=ctx.short_id, video_id=sh.video_id, priority=PRIORITY["render"],
                    dedupe_key=f"render:{ctx.short_id}")
        return {"status": report.status, "repairs": notes}
    final_status = report.status
    with session_scope() as s:
        sh = s.get(Short, ctx.short_id)
        sh.qc_status = final_status
        sh.qc_report = {**report.to_dict(), "repairs_applied": attempts}
        sh.fingerprint = {"dhash": fingerprint}
        if final_status == "FAIL":
            sh.status = "failed"
            sh.error = "; ".join(i.message for i in report.issues if i.level == "FAIL")[:500]
    if final_status == "FAIL":
        app.notify("render_failed", "Short failed quality control", report.issues[0].message if report.issues else "",
                   level="error", link=f"/shorts/{ctx.short_id}")
        return {"status": final_status}
    ctx.enqueue("short_metadata", short_id=ctx.short_id, video_id=sh.video_id, priority=PRIORITY["metadata"],
                dedupe_key=f"meta:{ctx.short_id}")
    return {"status": final_status, "issues": len(report.issues), "repairs": repairs}


@job_handler("short_metadata", resource="gpu", max_retries=2, priority=PRIORITY["metadata"], label="Metadata")
def short_metadata(ctx: JobContext) -> dict[str, Any]:
    app = get_context()
    settings = app.settings()
    with session_scope() as s:
        sh = s.get(Short, ctx.short_id)
        v = s.get(Video, sh.video_id)
        c = s.get(CandidateClip, sh.candidate_id) if sh.candidate_id else None
        row = current_timeline(s, ctx.short_id)
        tl = EditTimeline.model_validate(row.data)
        transcript = " ".join(w.text for w in tl.captions.words)
        video_title, channel = v.title, v.channel_name
        cand_title = c.title if c else None
        out = Path(sh.output_path)
        category = (c.labels or {}).get("category") if c else None
        existing_meta = sh.metadata_options or {}
    mode = ctx.payload.get("mode") or {"tech": "tech", "podcast": "podcast", "humor": "funny", "education": "educational",
                                       "science": "educational"}.get(category or "", "clean")
    llm = app.llm(settings)
    from shortforge.core.gpu import models as mm

    with mm.gpu_session("metadata"):
        meta = generate_metadata(llm, transcript, video_title=video_title, channel=channel, candidate_title=cand_title,
                                 mode=mode, system=settings.prompts.metadata or None)
        if llm is not None and settings.llm.unload_after_use:
            llm.unload()
    ctx.progress(0.7, "Selecting cover frame")
    cover = app.paths.thumbnails / f"short_{ctx.short_id}.jpg"
    try:
        pick_cover_frame(out, app.face_detector(), cover)
    except Exception as exc:
        ctx.log(f"cover selection failed: {exc}", "warning")
    with session_scope() as s:
        sh = s.get(Short, ctx.short_id)
        sh.title = meta.titles[0] if meta.titles else sh.title
        sh.description = meta.description
        sh.hashtags = meta.hashtags
        sh.metadata_options = {**existing_meta, "metadata": meta.to_dict(), "selected_title": 0}
        if cover.exists():
            sh.cover_path = sh.thumbnail_path = str(cover)
        qc = sh.qc_status
        ap = settings.autopilot
        sh.status = "ready" if qc == "PASS" or not (ap.enabled and ap.require_qc_pass) else "review"
        status = sh.status
        title = sh.title
    app.notify("short_ready", "Short ready", title, link=f"/shorts/{ctx.short_id}")
    if status == "ready" and settings.autopilot.enabled and settings.autopilot.auto_upload:
        from shortforge.engines.publishing.youtube_auth import load_credentials

        try:
            creds = load_credentials()
        except Exception:
            creds = None
        if creds is not None:
            schedule_short(ctx.short_id)
        else:
            ctx.log("auto-upload enabled but no YouTube account connected", "warning")
    return {"titles": meta.titles, "source": meta.source, "status": status}


# ============================================================================ publishing
def schedule_short(short_id: int, at: datetime | None = None, visibility: str | None = None) -> int:
    """Create an Upload for a Short at the next free slot (or at ``at``) and queue the upload job."""
    from shortforge.engines.publishing.scheduler import next_slot

    app = get_context()
    settings = app.settings()
    ap = settings.autopilot
    with session_scope() as s:
        sh = s.get(Short, short_id)
        if sh is None or not sh.output_path:
            raise ShortForgeError("Render the Short before scheduling it.")
        existing = s.execute(select(Upload).where(Upload.short_id == short_id,
                                                  Upload.status.in_(("scheduled", "uploading", "processing", "uploaded")))).scalar()
        if existing:
            return existing.id
        taken = [u.publish_at or u.scheduled_at for u in s.execute(
            select(Upload).where(Upload.status.in_(("scheduled", "uploading", "processing", "uploaded")),
                                 Upload.publish_at >= utcnow() - timedelta(days=2))).scalars() if (u.publish_at or u.scheduled_at)]
        now = utcnow()
        when = at or next_slot(now, strategy=ap.schedule_strategy, slots=ap.schedule_slots, taken=taken,
                               max_per_day=ap.max_uploads_per_day, min_gap_min=ap.min_upload_gap_min)
        meta = (sh.metadata_options or {}).get("metadata", {})
        tags = [h.lstrip("#") for h in (sh.hashtags or [])] + list(meta.get("keywords", []))[:10]
        hashtags = " ".join(sh.hashtags or [])
        description = (sh.description or "") + (f"\n\n{hashtags}" if hashtags else "")
        up = Upload(short_id=short_id, status="scheduled", scheduled_at=when,
                    publish_at=when if ap.upload_mode == "publish_at" else None,
                    visibility=visibility or ap.default_visibility, made_for_kids=ap.made_for_kids,
                    playlist_id=ap.playlist_id, title=sh.title, description=description, tags=tags)
        s.add(up)
        s.flush()
        upload_id = up.id
        sh.status = "scheduled"
    not_before = None if ap.upload_mode == "publish_at" else when
    app.queue.enqueue("upload_short", {"upload_id": upload_id}, short_id=short_id, priority=PRIORITY["upload"],
                      not_before=not_before, dedupe_key=f"upload:{upload_id}")
    app.bus.publish("short.updated", {"short_id": short_id, "status": "scheduled"})
    return upload_id


@job_handler("upload_short", resource="network", max_retries=5, priority=PRIORITY["upload"], label="Upload")
def upload_short(ctx: JobContext) -> dict[str, Any]:
    from shortforge.engines.publishing.uploader import UploadRequest, processing_status, upload_video
    from shortforge.engines.publishing.youtube_auth import load_credentials

    app = get_context()
    creds = load_credentials()
    if creds is None:
        raise DependencyMissing("Connect a YouTube account in Settings -> Publishing to upload.")
    upload_id = ctx.payload["upload_id"]
    with session_scope() as s:
        up = s.get(Upload, upload_id)
        sh = s.get(Short, up.short_id)
        if up.youtube_video_id:
            return {"youtube_video_id": up.youtube_video_id, "already": True}
        req = UploadRequest(path=Path(sh.output_path), title=up.title, description=up.description or "",
                            tags=up.tags or [], privacy=up.visibility, publish_at=up.publish_at,
                            made_for_kids=up.made_for_kids, playlist_id=up.playlist_id,
                            thumbnail=Path(sh.cover_path) if sh.cover_path else None)
        up.status, up.started_at, up.attempts = "uploading", utcnow(), up.attempts + 1
        sh.status = "uploading"
    app.bus.publish("short.updated", {"short_id": ctx.short_id, "status": "uploading"})

    def on_progress(p) -> None:  # type: ignore[no-untyped-def]
        ctx.progress(p.fraction * 0.95, f"Uploading · {p.speed_bps / 1e6:.1f} MB/s")
        with session_scope() as s:
            u = s.get(Upload, upload_id)
            u.progress, u.bytes_sent, u.speed_bps = p.fraction, p.bytes_sent, p.speed_bps

    try:
        vid = upload_video(creds, req, progress=on_progress, cancel=ctx.cancel)
    except Exception as exc:
        with session_scope() as s:
            u = s.get(Upload, upload_id)
            u.status = "scheduled" if getattr(exc, "retryable", False) else "failed"
            u.error = getattr(exc, "message", str(exc))[:500]
            sh = s.get(Short, u.short_id)
            sh.status = "scheduled" if u.status == "scheduled" else "failed"
        if not getattr(exc, "retryable", False):
            app.notify("upload_failed", "Upload failed", getattr(exc, "message", str(exc))[:200], level="error",
                       link=f"/shorts/{ctx.short_id}")
        raise
    state = {}
    with contextlib.suppress(Exception):
        state = processing_status(creds, vid)
    with session_scope() as s:
        u = s.get(Upload, upload_id)
        u.youtube_video_id, u.status, u.progress, u.finished_at = vid, "uploaded", 1.0, utcnow()
        u.processing_status = state.get("processing")
        sh = s.get(Short, u.short_id)
        sh.status = "published" if not u.publish_at or u.publish_at <= utcnow() else "scheduled"
        title = sh.title
    app.notify("upload_completed", "Upload complete", title, link=f"https://youtube.com/shorts/{vid}")
    return {"youtube_video_id": vid, "processing": state}


# ============================================================================ analytics + learning
def _learning_features(s, sh: Short, up: Upload) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    row = current_timeline(s, sh.id)
    tl = EditTimeline.model_validate(row.data) if row else None
    c = s.get(CandidateClip, sh.candidate_id) if sh.candidate_id else None
    v = s.get(Video, sh.video_id) if sh.video_id else None
    first3 = [w for w in (tl.captions.words if tl else []) if w.start < 3.0]
    report = (sh.metadata_options or {}).get("render_report") or {}
    publish = up.publish_at or up.finished_at
    return {
        "duration": sh.duration, "hook_type": (c.labels or {}).get("hook_type") if c else None,
        "category": (c.labels or {}).get("category") if c else None,
        "opening_wps": round(len(first3) / 3.0, 2), "caption_preset": sh.caption_preset,
        "source": v.channel_name if v else None, "score": sh.score,
        "cuts": len(tl.ranges) - 1 if tl else None, "zooms_per_min": round(len(tl.zooms) / max(0.1, tl.duration / 60), 2) if tl else None,
        "speaker_count": (report.get("reframe") or {}).get("speaker_switches"),
        "upload_hour": publish.astimezone().hour if publish else None,
    }


@job_handler("refresh_analytics", resource="network", max_retries=2, priority=30, label="Analytics")
def refresh_analytics(ctx: JobContext) -> dict[str, Any]:
    from shortforge.engines.analytics.youtube_stats import fetch_retention, fetch_statistics
    from shortforge.engines.publishing.youtube_auth import load_credentials

    creds = load_credentials()
    if creds is None:
        raise DependencyMissing("Connect a YouTube account to collect analytics.")
    with session_scope() as s:
        ups = s.execute(select(Upload).where(Upload.youtube_video_id.is_not(None))).scalars().all()
        ids = {u.youtube_video_id: (u.short_id, u.id) for u in ups}
        oldest = min((u.finished_at for u in ups if u.finished_at), default=utcnow()).date()
    if not ids:
        return {"videos": 0}
    stats = fetch_statistics(creds, list(ids))
    retention = fetch_retention(creds, list(ids), oldest)
    with session_scope() as s:
        for vid, st in stats.items():
            short_id, upload_id = ids[vid]
            ret = retention.get(vid, {})
            s.add(AnalyticsSnapshot(short_id=short_id, youtube_video_id=vid, views=st.get("views"), likes=st.get("likes"),
                                    comments=st.get("comments"), average_view_duration=ret.get("average_view_duration"),
                                    average_view_percentage=ret.get("average_view_percentage")))
            sh, up = s.get(Short, short_id), s.get(Upload, upload_id)
            lf = s.execute(select(LearningFeature).where(LearningFeature.short_id == short_id)).scalar()
            if lf is None:
                lf = LearningFeature(short_id=short_id, features={})
                s.add(lf)
            lf.features = _learning_features(s, sh, up)
            if up.publish_at and up.publish_at <= utcnow() and sh.status == "scheduled":
                sh.status = "published"
    get_context().bus.publish("analytics.updated", {})
    return {"videos": len(stats), "retention": len(retention)}


@job_handler("learning_update", resource="cpu", max_retries=0, priority=20, label="Learning")
def learning_update(ctx: JobContext) -> dict[str, Any]:
    from shortforge.engines.learning.optimizer import Sample, update_weights

    app = get_context()
    settings = app.settings()
    if not settings.learning.enabled:
        return {"skipped": "learning disabled"}
    samples: list[Sample] = []
    with session_scope() as s:
        for lf in s.execute(select(LearningFeature)).scalars():
            snap = s.execute(select(AnalyticsSnapshot).where(AnalyticsSnapshot.short_id == lf.short_id)
                             .order_by(AnalyticsSnapshot.fetched_at.desc()).limit(1)).scalar()
            up = s.execute(select(Upload).where(Upload.short_id == lf.short_id, Upload.youtube_video_id.is_not(None))).scalar()
            sh = s.get(Short, lf.short_id)
            if not snap or not up or not sh or not sh.candidate_id:
                continue
            metrics = {cs.metric: cs.value for cs in s.execute(select(ClipScore).where(
                ClipScore.candidate_id == sh.candidate_id, ClipScore.kind == "score")).scalars()}
            published = up.publish_at or up.finished_at or snap.fetched_at
            age = (snap.fetched_at - published).total_seconds() / 3600
            samples.append(Sample(sh.id, metrics, lf.features, snap.views or 0, snap.likes, age))
        current = ranking_weights(s)
    result = update_weights(samples, current, min_samples=settings.learning.min_samples,
                            learning_rate=settings.learning.learning_rate, max_change=settings.learning.max_weight_change)
    with session_scope() as s:
        if result.updated:
            set_value(s, "ranking_weights", result.weights)
        set_value(s, "learning_state", {"updated_at": utcnow().isoformat(), "samples": result.samples,
                                        "reason": result.reason, "correlations": result.correlations,
                                        "insights": result.insights, "previous": result.previous,
                                        "labels": METRIC_LABELS})
    return {"updated": result.updated, "samples": result.samples, "reason": result.reason}


# ============================================================================ maintenance
@job_handler("install_model", resource="network", max_retries=2, priority=90, label="Install model")
def install_model(ctx: JobContext) -> dict[str, Any]:
    app = get_context()
    model_id = ctx.payload["model_id"]
    app.model_store().install(model_id, progress=ctx.sub(0, 1), cancelled=lambda: ctx.cancel.cancelled)
    if model_id.startswith("vision:"):
        app.reset_detector()
    app.notify("model_download", "Model installed", model_id)
    app.bus.publish("models.updated", {})
    return {"model": model_id}


@job_handler("storage_cleanup", resource="io", max_retries=0, priority=10, label="Cleanup")
def storage_cleanup(ctx: JobContext) -> dict[str, Any]:
    """Apply the storage policy. Final renders are never deleted unless explicitly configured."""
    import time as _time

    app = get_context()
    st = app.settings().storage
    freed = 0
    cutoff = _time.time() - st.temp_max_age_h * 3600
    for p in app.paths.temp.glob("*"):
        try:
            if p.stat().st_mtime < cutoff:
                size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else p.stat().st_size
                shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
                freed += size
        except OSError:
            continue
    with session_scope() as s:
        done_videos = s.execute(select(Video).where(Video.processing_status == "analyzed")).scalars().all()
        for v in done_videos:
            pending = s.execute(select(func.count(Short.id)).where(
                Short.video_id == v.id, Short.status.in_(("draft", "rendering")))).scalar()
            if pending:
                continue
            if not st.keep_source_media and v.local_path and v.youtube_id and Path(v.local_path).exists():
                freed += Path(v.local_path).stat().st_size
                Path(v.local_path).unlink(missing_ok=True)
                v.download_status = "purged"
            if not st.keep_proxy_media and v.proxy_path and Path(v.proxy_path).exists():
                freed += Path(v.proxy_path).stat().st_size
                Path(v.proxy_path).unlink(missing_ok=True)
                v.proxy_path = None
    # Enforce the cache cap by evicting least-recently-used per-video caches (they can be rebuilt).
    caches = sorted((p for p in app.paths.cache.glob("video_*") if p.is_dir()), key=lambda p: p.stat().st_mtime)
    total = sum(f.stat().st_size for p in caches for f in p.rglob("*") if f.is_file())
    cap = st.max_cache_gb * 1024**3
    for p in caches:
        if total <= cap:
            break
        size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        shutil.rmtree(p, ignore_errors=True)
        total -= size
        freed += size
    return {"freed_mb": round(freed / 1024 / 1024, 1)}
