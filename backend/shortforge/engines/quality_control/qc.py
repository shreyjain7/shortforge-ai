"""Automatic quality control for rendered Shorts, with repair suggestions.

Returns PASS / WARNING / FAIL plus structured issues. Each fixable issue names a repair action that
the pipeline can apply to the edit timeline before re-rendering (bounded number of attempts).
"""

from __future__ import annotations

import itertools
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from shortforge.core.logging import get_logger
from shortforge.engines.captions.layout import LayoutConfig, Page, check_overflow
from shortforge.engines.editing.timeline import EditTimeline
from shortforge.engines.media import ffmpeg as ff
from shortforge.engines.reframing.camera import jitter_score
from shortforge.engines.vision.sampler import sample_frames

log = get_logger("qc")

LEVEL_ORDER = {"PASS": 0, "WARNING": 1, "FAIL": 2}


@dataclass
class Issue:
    check: str
    level: str  # WARNING | FAIL
    message: str
    repair: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class QCReport:
    status: str
    issues: list[Issue]
    metrics: dict[str, Any]
    checks_run: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "issues": [asdict(i) for i in self.issues], "metrics": self.metrics,
                "checks_run": self.checks_run}

    @property
    def repairs(self) -> list[str]:
        return sorted({i.repair for i in self.issues if i.repair})


def _media_analysis(path: Path, duration: float) -> dict[str, Any]:
    """One decode pass: black frames, frozen frames, loudness, true peak, volume stats."""
    stderr = ff.run_ffmpeg_capture(
        ["-i", str(path), "-vf", "blackdetect=d=0.4:pix_th=0.08,freezedetect=n=-50dB:d=2.5",
         "-af", "ebur128=peak=true,volumedetect", "-f", "null", "-"], timeout=max(120, duration * 6))
    out: dict[str, Any] = {"black": [], "freeze": []}
    for m in re.finditer(r"black_start:([\d.]+) black_end:([\d.]+) black_duration:([\d.]+)", stderr):
        out["black"].append((float(m.group(1)), float(m.group(2))))
    starts = [float(x) for x in re.findall(r"freeze_start: ([\d.]+)", stderr)]
    durs = [float(x) for x in re.findall(r"freeze_duration: ([\d.]+)", stderr)]
    out["freeze"] = list(zip(starts, durs, strict=False))
    summary = stderr[stderr.rfind("Summary:"):] if "Summary:" in stderr else ""
    m = re.search(r"I:\s+(-?[\d.]+|-inf) LUFS", summary)
    out["integrated_lufs"] = float(m.group(1)) if m and m.group(1) != "-inf" else None
    m = re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", summary)
    out["true_peak_db"] = float(m.group(1)) if m and m.group(1) != "-inf" else None
    m = re.search(r"max_volume: (-?[\d.]+) dB", stderr)
    out["max_volume_db"] = float(m.group(1)) if m else None
    m = re.search(r"mean_volume: (-?[\d.]+) dB", stderr)
    out["mean_volume_db"] = float(m.group(1)) if m else None
    return out


def dhash_sequence(path: Path, fps: float = 1.0) -> list[int]:
    """Per-second 64-bit difference hashes (visual fingerprint for duplicate detection)."""
    hashes = []
    for _, frame in sample_frames(path, 0, 10_000, fps, width=160):
        g = cv2.cvtColor(cv2.resize(frame, (9, 8), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
        bits = (g[:, 1:] > g[:, :-1]).flatten()
        hashes.append(int("".join("1" if b else "0" for b in bits), 2))
    return hashes


def run_qc(output: Path, timeline: EditTimeline, pages: list[Page] | None, *,
           crop_path: list[tuple[float, float, float, float, float]] | None = None,
           words_prob: list[tuple[str, float, float]] | None = None,
           speech_out: list[tuple[float, float]] | None = None,
           existing_fingerprints: dict[int, list[int]] | None = None,
           face_track: list[tuple[float, float, float, float, float]] | None = None) -> tuple[QCReport, list[int]]:
    """Validate a rendered Short. ``face_track`` = (t_out, x, y, w, h) of the followed face in source px."""
    issues: list[Issue] = []
    metrics: dict[str, Any] = {}
    checks: list[str] = []

    # ---------------- container / stream checks
    checks.append("output_valid")
    if not output.exists() or output.stat().st_size < 10_000:
        issues.append(Issue("output_valid", "FAIL", "Output file is missing or empty.", "rerender"))
        return QCReport("FAIL", issues, metrics, checks), []
    try:
        info = ff.probe(output)
    except ff.FFmpegError:
        issues.append(Issue("output_valid", "FAIL", "Output file cannot be read (corrupt).", "rerender"))
        return QCReport("FAIL", issues, metrics, checks), []
    metrics.update(width=info.width, height=info.height, fps=round(info.fps, 3), duration=round(info.duration, 3),
                   size=info.size, vcodec=info.vcodec, acodec=info.acodec)
    checks += ["dimensions", "fps", "duration", "streams"]
    if (info.width, info.height) != (timeline.width, timeline.height):
        issues.append(Issue("dimensions", "FAIL", f"Output is {info.width}x{info.height}, expected "
                            f"{timeline.width}x{timeline.height}.", "rerender"))
    if abs(info.fps - timeline.fps) > 0.1:
        issues.append(Issue("fps", "FAIL", f"Output FPS {info.fps:.2f} differs from {timeline.fps:.2f}.", "rerender"))
    if abs(info.duration - timeline.duration) > max(0.25, 2 / timeline.fps):
        issues.append(Issue("duration", "WARNING", f"Duration {info.duration:.2f}s vs timeline "
                            f"{timeline.duration:.2f}s."))
    if not info.has_audio:
        issues.append(Issue("streams", "FAIL", "Output has no audio track.", "rerender"))

    # ---------------- decode-level analysis
    checks += ["black_frames", "frozen_frames", "audio_clipping", "silent_audio", "loudness"]
    try:
        media = _media_analysis(output, info.duration)
    except ff.FFmpegError as exc:
        issues.append(Issue("corrupt_frames", "FAIL", "Decoding the output reported errors.", "rerender",
                            {"detail": exc.detail}))
        media = {"black": [], "freeze": []}
    black_total = sum(e - s for s, e in media["black"])
    metrics["black_seconds"] = round(black_total, 2)
    if black_total > 0.3 * info.duration:
        issues.append(Issue("black_frames", "FAIL", f"{black_total:.1f}s of black frames.", "rerender"))
    elif media["black"]:
        issues.append(Issue("black_frames", "WARNING", f"Black frames detected ({black_total:.1f}s)."))
    freeze_total = sum(d for _, d in media["freeze"])
    metrics["frozen_seconds"] = round(freeze_total, 2)
    if freeze_total > 3.0:
        issues.append(Issue("frozen_frames", "WARNING", f"Video appears frozen for {freeze_total:.1f}s."))
    for key in ("integrated_lufs", "true_peak_db", "max_volume_db", "mean_volume_db"):
        metrics[key] = media.get(key)
    if media.get("max_volume_db") is not None and media["max_volume_db"] >= -0.05:
        issues.append(Issue("audio_clipping", "WARNING", "Audio reaches 0 dBFS (possible clipping).", "lower_gain"))
    if media.get("mean_volume_db") is not None and media["mean_volume_db"] < -50:
        issues.append(Issue("silent_audio", "FAIL", "Audio is effectively silent.", None))
    lufs = media.get("integrated_lufs")
    if lufs is not None and abs(lufs - timeline.audio.target_lufs) > 3:
        issues.append(Issue("loudness", "WARNING", f"Loudness {lufs:.1f} LUFS (target {timeline.audio.target_lufs})."))

    # ---------------- captions
    if pages is not None and timeline.captions.enabled:
        checks += ["caption_overflow", "caption_safe_zone", "caption_timing"]
        cfg = LayoutConfig(timeline.width, timeline.height)
        cfg.safe.top, cfg.safe.bottom = timeline.safe_area.top, timeline.safe_area.bottom
        cfg.safe.left, cfg.safe.right = timeline.safe_area.left, timeline.safe_area.right
        overflow = check_overflow(pages, cfg)
        if overflow:
            issues.append(Issue("caption_overflow", "FAIL", overflow[0] + (f" (+{len(overflow) - 1} more)" if len(overflow) > 1 else ""),
                                "shrink_captions"))
        prev_end = -1.0
        timing_errors = 0
        for p in pages:
            if p.end - p.start < 0.12:
                timing_errors += 1
            for w in p.words:
                if w.end < w.start or w.start < prev_end - 0.05 or w.end > timeline.duration + 0.05:
                    timing_errors += 1
                prev_end = max(prev_end, w.start)
        if timing_errors:
            issues.append(Issue("caption_timing", "FAIL" if timing_errors > 3 else "WARNING",
                                f"{timing_errors} caption timing anomalies.", "regenerate_captions"))
        words_text = [w.text for w in timeline.captions.words]
        if words_text and not re.search(r"[.!?…]['\")]*$", words_text[-1]):
            checks.append("unfinished_sentence")
            issues.append(Issue("unfinished_sentence", "WARNING", "The clip ends without finishing a sentence."))
        stutters = [words_text[i] for i in range(2, len(words_text))
                    if words_text[i].lower().strip(".,!?") == words_text[i - 1].lower().strip(".,!?")
                    == words_text[i - 2].lower().strip(".,!?")]
        if stutters:
            issues.append(Issue("subtitle_spelling", "WARNING", f"Repeated words in captions: {', '.join(stutters[:3])}"))
    if words_prob:
        checks.append("subtitle_spelling")
        low = [w for w, p, _ in words_prob if p < 0.35 and len(w.strip()) > 2]
        metrics["low_confidence_words"] = len(low)
        if len(low) > max(3, 0.08 * len(words_prob)):
            issues.append(Issue("subtitle_spelling", "WARNING", f"{len(low)} low-confidence words, e.g. "
                                f"{', '.join(low[:4])} - review captions."))

    # ---------------- editing structure
    checks += ["bad_cuts", "short_shots", "punch_ins", "dead_sections", "resolution"]
    cut_pts = timeline.cut_points()
    shots = [b - a for a, b in zip([0.0, *cut_pts], [*cut_pts, timeline.duration], strict=False)]
    very_short = [s for s in shots if s < 0.4]
    if very_short:
        issues.append(Issue("short_shots", "WARNING", f"{len(very_short)} very short shot(s) under 0.4s."))
    per_min = len(timeline.zooms) / max(0.1, timeline.duration / 60)
    metrics["punch_ins_per_min"] = round(per_min, 1)
    if per_min > 12:
        issues.append(Issue("punch_ins", "WARNING", f"{per_min:.0f} punch-ins per minute feels over-edited.",
                            "reduce_zooms"))
    if speech_out and timeline.captions.words:
        gaps = [b - a for a, b in zip([0.0] + [e for _, e in speech_out], [s for s, _ in speech_out] + [timeline.duration], strict=False)]
        longest = max(gaps) if gaps else 0.0
        metrics["longest_silence"] = round(longest, 2)
        if longest > 2.0:
            issues.append(Issue("dead_sections", "WARNING", f"{longest:.1f}s without speech."))
    if timeline.source_height < 720:
        issues.append(Issue("resolution", "WARNING", f"Low-resolution source ({timeline.source_height}p) - "
                            "output will look soft."))

    # ---------------- camera quality
    if crop_path and len(crop_path) > 5:
        checks.append("crop_jitter")
        xs = [(x + w / 2) / timeline.source_width for _, x, _, w, _ in crop_path]
        dt = (crop_path[-1][0] - crop_path[0][0]) / max(1, len(crop_path) - 1)
        # Remove intentional hard cuts before measuring jitter.
        segs, cur = [], [xs[0]]
        for a, b in itertools.pairwise(xs):
            if abs(b - a) > 0.08:
                segs.append(cur)
                cur = []
            cur.append(b)
        segs.append(cur)
        jit = max((jitter_score(s, dt) for s in segs if len(s) > 3), default=0.0)
        metrics["crop_jitter"] = round(jit, 3)
        if jit > 1.5:
            issues.append(Issue("crop_jitter", "WARNING", "Crop path is jittery.", "smooth_camera"))
    if face_track and crop_path:
        checks.append("face_cropping")
        cut = 0
        for t, fx, _fy, fw, _fh in face_track:
            rect = min(crop_path, key=lambda r: abs(r[0] - t))
            _, rx, _ry, rw, _rh = rect
            inside = max(0.0, min(fx + fw, rx + rw) - max(fx, rx)) / max(1e-6, fw)
            if inside < 0.7:
                cut += 1
        ratio = cut / len(face_track)
        metrics["face_cut_ratio"] = round(ratio, 3)
        if ratio > 0.15:
            issues.append(Issue("face_cropping", "FAIL" if ratio > 0.35 else "WARNING",
                                f"The subject's face is cut off in {ratio:.0%} of sampled frames.", "replan_reframe"))

    # ---------------- duplicates
    fingerprint: list[int] = []
    try:
        fingerprint = dhash_sequence(output)
    except Exception as exc:
        log.debug("fingerprint failed: %s", exc)
    if existing_fingerprints and fingerprint:
        from shortforge.engines.clip_detection.dedupe import fingerprint_similarity

        checks.append("duplicate")
        best = max(((sid, fingerprint_similarity(fingerprint, fp)) for sid, fp in existing_fingerprints.items()),
                   key=lambda x: x[1], default=(None, 0.0))
        metrics["max_similarity"] = round(best[1], 3)
        if best[1] >= 0.9:
            issues.append(Issue("duplicate", "WARNING", f"Visually {best[1]:.0%} similar to Short #{best[0]}.",
                                data={"short_id": best[0]}))
    status = "PASS"
    for i in issues:
        if LEVEL_ORDER[i.level] > LEVEL_ORDER[status]:
            status = i.level
    return QCReport(status, issues, metrics, checks), fingerprint


def apply_repairs(timeline: EditTimeline, repairs: list[str]) -> tuple[EditTimeline, list[str]]:
    """Return a repaired copy of the timeline and a description of what changed."""
    tl = timeline.model_copy(deep=True)
    notes: list[str] = []
    for r in repairs:
        if r == "shrink_captions":
            cur = tl.captions.max_words or 3
            tl.captions.max_words = max(1, cur - 1)
            scale = float(tl.captions.overrides.get("font_scale", 1.0)) * 0.9
            tl.captions.overrides["font_scale"] = round(scale, 3)
            notes.append("recomputed caption line breaks with smaller pages/font")
        elif r == "lower_gain":
            # Loudnorm re-normalises gain, so tighten the ceiling (limiter) and loudness progressively.
            tl.audio.true_peak = round(min(tl.audio.true_peak, -1.0) - 2.0, 2)
            tl.audio.target_lufs = round(tl.audio.target_lufs - 1.0, 2)
            notes.append(f"tightened the peak ceiling to {tl.audio.true_peak} dBTP and loudness to "
                         f"{tl.audio.target_lufs} LUFS")
        elif r == "reduce_zooms":
            tl.zooms = tl.zooms[::2]
            notes.append("halved the number of punch-ins")
        elif r == "smooth_camera":
            tl.notes.append("repair:smooth_camera")
            notes.append("requested a smoother camera path")
        elif r == "replan_reframe":
            tl.notes.append("repair:replan_reframe")
            notes.append("requested a wider, more conservative reframe")
        elif r == "regenerate_captions":
            tl.notes.append("repair:regenerate_captions")
            notes.append("regenerated caption timing from word timestamps")
        elif r == "rerender":
            notes.append("re-rendering")
    return tl, notes


def score_cover_frame(frame: np.ndarray, faces: list[Any]) -> float:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    sharp = min(1.0, cv2.Laplacian(gray, cv2.CV_64F).var() / 400.0)
    bright = 1.0 - abs(float(gray.mean()) - 125) / 125
    face = 0.0
    if faces:
        f = max(faces, key=lambda b: b.w)
        size = min(1.0, f.w / 0.35)
        upper = 1.0 - min(1.0, abs(f.cy - 0.38) / 0.4)
        face = 0.6 * size + 0.4 * upper
    return 0.45 * face + 0.35 * sharp + 0.2 * bright
