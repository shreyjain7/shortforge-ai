"""Automatic editor: candidate clip -> EditTimeline.

Decisions made here are the "editor's taste": trim dead air only between words, keep natural
breathing room, frame the active speaker, punch in on key moments only, and caption with
semantic emphasis. Everything is written to a non-destructive timeline the user can adjust.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from shortforge.core.config import AppSettings
from shortforge.core.logging import get_logger
from shortforge.engines.audio.analysis import plan_silence_cuts
from shortforge.engines.audio.music import pick_track, snap_to_beats
from shortforge.engines.captions.emphasis import select_emphasis, word_importance
from shortforge.engines.editing.broll import match_broll
from shortforge.engines.editing.timeline import (
    AudioSpec,
    CaptionTrack,
    CaptionWord,
    EditTimeline,
    EnhanceSpec,
    MusicTrack,
    SafeArea,
    SourceRange,
    TextOverlay,
)
from shortforge.engines.reframing.planner import ReframeConfig, plan_punch_ins, plan_reframe
from shortforge.engines.transcription.types import Word
from shortforge.engines.vision.faces import FaceDetector

log = get_logger("auto_editor")


@dataclass
class ClipSpec:
    source_path: str
    analysis_video: str
    """Video used for visual analysis (the proxy when available: same timeline, faster to decode)."""
    source_width: int
    source_height: int
    source_fps: float
    start: float
    end: float
    words: list[Word]
    scene_cuts: list[float]
    speech: list[tuple[float, float]]
    keywords: list[str] = field(default_factory=list)
    hook_text: str | None = None
    idf: dict[str, float] | None = None
    caption_preset: str | None = None
    reframe_mode: str | None = None
    broll_clips: list = field(default_factory=list)
    music_tracks: list = field(default_factory=list)


def output_fps(source_fps: float, setting: str) -> float:
    if setting in ("30", "60"):
        return float(setting)
    if not source_fps or source_fps < 20:
        return 30.0
    if source_fps > 61:
        return 60.0
    # Keep NTSC rates exact (29.97 / 59.94) to avoid judder from rate conversion.
    return round(source_fps, 3)


def build_timeline(spec: ClipSpec, settings: AppSettings, detector: FaceDetector | None) -> tuple[EditTimeline, dict]:
    clip_words = [w for w in spec.words if w.end > spec.start and w.start < spec.end]
    if settings.clips.silence_trim:
        ranges = plan_silence_cuts([(w.start, w.end) for w in clip_words], spec.start, spec.end,
                                   settings.clips.pacing)
    else:
        ranges = [(spec.start, spec.end)]
    tl = EditTimeline(
        source_path=spec.source_path,
        source_width=spec.source_width,
        source_height=spec.source_height,
        fps=output_fps(spec.source_fps, settings.render.fps),
        width=settings.render.width,
        height=settings.render.height,
        ranges=[SourceRange(start=a, end=b) for a, b in ranges],
        audio=AudioSpec(target_lufs=settings.audio.target_lufs, true_peak=settings.audio.true_peak,
                        fade_ms=settings.audio.fade_ms, compressor=settings.audio.compressor, eq=settings.audio.eq,
                        denoise=settings.audio.denoise),
        enhance=EnhanceSpec(**settings.enhance.model_dump()),
        safe_area=SafeArea(**settings.safe_area.model_dump()),
    )
    report: dict = {"ranges": len(tl.ranges), "trimmed_s": round((spec.end - spec.start) - tl.duration, 2)}

    # ---- captions: map source word times to the output timeline
    texts = [w.clean for w in clip_words]
    emphasis = select_emphasis(texts, spec.keywords, spec.idf) if settings.captions.emphasis else [False] * len(texts)
    cap_words: list[CaptionWord] = []
    for w, emph in zip(clip_words, emphasis, strict=False):
        s = tl.source_to_output(max(w.start, spec.start))
        e = tl.source_to_output(min(w.end, spec.end))
        if s is None:
            continue
        if e is None or e <= s:
            e = s + max(0.05, min(w.end, spec.end) - max(w.start, spec.start))
        cap_words.append(CaptionWord(text=w.clean, start=round(s, 3), end=round(min(e, tl.duration), 3), emphasis=emph,
                                     src_start=round(w.start, 3), src_end=round(w.end, 3)))
    tl.captions = CaptionTrack(enabled=settings.captions.enabled,
                               preset=spec.caption_preset or settings.captions.preset, words=cap_words,
                               vertical_position=settings.captions.vertical_position)

    # ---- hook overlay (only grounded text from the clip itself)
    if spec.hook_text:
        tl.overlays.append(TextOverlay(text=spec.hook_text, start=0.0, end=min(2.8, tl.duration * 0.3), kind="hook"))

    # ---- reframing
    rcfg = ReframeConfig(mode=spec.reframe_mode or settings.reframe.mode, sample_fps=settings.reframe.sample_fps,
                         deadzone=settings.reframe.deadzone, min_hold=settings.reframe.min_hold_s,
                         punch_ins=settings.reframe.punch_ins, max_punch_in=settings.reframe.max_punch_in)
    if detector is not None and Path(spec.analysis_video).exists():
        cuts_in = [c for c in spec.scene_cuts if spec.start < c < spec.end]
        layouts, keys, stats = plan_reframe(Path(spec.analysis_video), tl, detector, cuts_in, spec.speech, rcfg)
        tl.layouts, tl.crop = layouts, keys
        report["reframe"] = stats
    else:
        from shortforge.engines.editing.timeline import CropKey, LayoutSegment

        tl.layouts = [LayoutSegment(start=0.0, end=tl.duration, layout="crop")]
        tl.crop = [CropKey(t=0.0, cx=0.5), CropKey(t=tl.duration, cx=0.5)]
        report["reframe"] = {"skipped": "no face detector"}

    # ---- punch-ins at the strongest emphasis moments
    scores = word_importance(texts, spec.keywords, spec.idf)
    strong = sorted(((scores[i], cap_words[i].start) for i in range(min(len(scores), len(cap_words)))
                     if emphasis[i] and scores[i] >= 3.5), reverse=True)
    emphasis_times = sorted(t for _, t in strong[: max(1, int(tl.duration / 8))])
    tl.zooms = plan_punch_ins(tl, emphasis_times, rcfg, spec.source_height)
    report["punch_ins"] = len(tl.zooms)

    # ---- B-roll matched to what is being said
    if settings.broll.enabled and spec.broll_clips:
        inserts = match_broll(tl.captions.words, spec.broll_clips, total=tl.duration,
                              max_inserts=settings.broll.max_inserts, insert_duration=settings.broll.insert_duration)
        if settings.broll.mode == "auto":
            tl.broll = inserts
        else:
            tl.broll_suggestions = inserts
        report["broll"] = {"mode": settings.broll.mode, "matches": len(inserts)}

    # ---- optional music bed, ducked under speech, with punch-ins landing on beats
    if settings.music.enabled and spec.music_tracks:
        words_per_s = len(cap_words) / max(1.0, tl.duration)
        track = pick_track(spec.music_tracks, tl.duration, clip_energy=min(1.0, words_per_s / 4.0),
                           seed=int(spec.start * 10))
        if track is not None:
            offset = track.beats[0] if track.beats else 0.0
            tl.music = MusicTrack(path=track.path, volume_db=settings.music.volume_db, duck_db=settings.music.duck_db,
                                  offset=round(offset, 3))
            starts = snap_to_beats([z.start for z in tl.zooms], track.beats, offset)
            for z, s in zip(tl.zooms, starts, strict=False):
                shift = s - z.start
                z.start, z.end = round(s, 3), round(min(tl.duration, z.end + shift), 3)
            report["music"] = {"track": Path(track.path).name, "tempo": track.tempo}
    return tl, report
