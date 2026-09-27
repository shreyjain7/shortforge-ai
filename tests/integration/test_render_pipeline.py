"""Real FFmpeg rendering on synthetic media: timeline -> captions -> 1080x1920 MP4 -> QC."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from shortforge.core.config import AppSettings
from shortforge.engines.captions.ass import build_ass, write_ass
from shortforge.engines.captions.layout import CapWord, LayoutConfig
from shortforge.engines.captions.presets import fonts_dir, get_preset
from shortforge.engines.editing.timeline import (
    CaptionTrack,
    CaptionWord,
    CropKey,
    EditTimeline,
    LayoutSegment,
    SourceRange,
    ZoomEvent,
)
from shortforge.engines.media import ffmpeg as ff
from shortforge.engines.quality_control.qc import run_qc
from shortforge.engines.rendering.renderer import RenderOptions, render_timeline
from tests.conftest import requires_ffmpeg

pytestmark = requires_ffmpeg


@pytest.fixture(scope="module")
def source_video(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("media") / "source.mp4"
    subprocess.run([ff.ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=8",
                    "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000:d=8",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(out)], check=True)
    return out


def _timeline(src: Path) -> EditTimeline:
    words = [CaptionWord(text=w, start=0.2 + i * 0.4, end=0.55 + i * 0.4, emphasis=w in ("twice", "fast"))
             for i, w in enumerate(["this", "graphics", "card", "is", "almost", "twice", "as", "fast", "as", "before"])]
    return EditTimeline(
        source_path=str(src), source_width=1280, source_height=720, fps=30.0,
        ranges=[SourceRange(start=1.0, end=3.0), SourceRange(start=4.0, end=6.5)],
        layouts=[LayoutSegment(start=0, end=2.0, layout="crop"), LayoutSegment(start=2.0, end=4.5, layout="fit")],
        crop=[CropKey(t=0, cx=0.3), CropKey(t=2.0, cx=0.7)],
        zooms=[ZoomEvent(start=0.5, end=1.8, scale=1.1)],
        captions=CaptionTrack(preset="Bold", words=words),
    )


def test_render_produces_valid_vertical_short(source_video: Path, tmp_path: Path) -> None:
    tl = _timeline(source_video)
    cfg = LayoutConfig(1080, 1920)
    content, pages = build_ass([CapWord(w.text, w.start, w.end, w.emphasis) for w in tl.captions.words],
                               get_preset("Bold"), cfg, clip_duration=tl.duration)
    ass = write_ass(tmp_path / "captions.ass", content)
    out = tmp_path / "short.mp4"
    result = render_timeline(tl, out, tmp_path / "work", options=RenderOptions(profile="FAST", encoder="cpu"),
                             ass_path=ass, fonts_dir=fonts_dir())
    info = ff.probe(out)
    assert (info.width, info.height) == (1080, 1920)
    assert abs(info.fps - 30) < 0.01 and info.has_audio and info.vcodec == "h264" and info.acodec == "aac"
    assert abs(info.duration - tl.duration) < 0.15
    assert result.crop_path
    report, fingerprint = run_qc(out, tl, pages, crop_path=result.crop_path)
    assert report.status in ("PASS", "WARNING"), report.to_dict()
    assert report.metrics["width"] == 1080 and fingerprint
    assert report.metrics["integrated_lufs"] is not None and abs(report.metrics["integrated_lufs"] + 14) < 2.5


def test_render_with_broll_music_split_and_repairs(source_video: Path, tmp_path: Path) -> None:
    """Exercise B-roll (full + PiP), music with sidechain ducking, split layout and QC repairs."""
    from shortforge.engines.editing.timeline import BrollInsert, CropKey, LayoutSegment, MusicTrack
    from shortforge.engines.quality_control.qc import apply_repairs

    broll = tmp_path / "broll.mp4"
    music = tmp_path / "music.wav"
    subprocess.run([ff.ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "mandelbrot=s=640x360:r=30",
                    "-t", "3", "-pix_fmt", "yuv420p", str(broll)], check=True)
    subprocess.run([ff.ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:sample_rate=48000:d=10", str(music)], check=True)
    tl = _timeline(source_video)
    tl.layouts = [LayoutSegment(start=0, end=2.0, layout="split",
                                secondary=[CropKey(t=0, cx=0.8, cy=0.5, zoom=1.35), CropKey(t=2, cx=0.8, cy=0.5, zoom=1.35)]),
                  LayoutSegment(start=2.0, end=4.5, layout="crop")]
    tl.crop = [CropKey(t=0, cx=0.2, cy=0.5, zoom=1.35), CropKey(t=2.0, cx=0.2, cy=0.5, zoom=1.35),
               CropKey(t=2.01, cx=0.5), CropKey(t=4.5, cx=0.5)]
    tl.broll = [BrollInsert(path=str(broll), start=0.5, end=1.5, mode="pip"),
                BrollInsert(path=str(broll), start=2.5, end=3.5, mode="full")]
    tl.music = MusicTrack(path=str(music), volume_db=-18, duck_db=-10)
    tl.enhance.sharpen = True
    tl.enhance.vignette = True
    repaired, notes = apply_repairs(tl, ["shrink_captions"])
    assert notes
    out = tmp_path / "rich.mp4"
    result = render_timeline(repaired, out, tmp_path / "work2", options=RenderOptions(profile="FAST", encoder="cpu"))
    info = ff.probe(out)
    assert (info.width, info.height) == (1080, 1920) and info.has_audio
    assert abs(result.duration - tl.duration) < 0.15


def test_qc_detects_wrong_dimensions(source_video: Path, tmp_path: Path) -> None:
    tl = _timeline(source_video)
    tl.width, tl.height = 720, 1280  # expect something different from the real file
    out = tmp_path / "wrong.mp4"
    subprocess.run([ff.ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(source_video), "-t", "2", str(out)], check=True)
    report, _ = run_qc(out, tl, None)
    assert report.status == "FAIL"
    assert any(i.check == "dimensions" and i.repair == "rerender" for i in report.issues)


def test_auto_editor_builds_timeline_without_detector(source_video: Path) -> None:
    from shortforge.engines.editing.auto_editor import ClipSpec, build_timeline
    from shortforge.engines.transcription.types import Word

    words = [Word(i, " " + w, 0.5 + i * 0.4, 0.8 + i * 0.4) for i, w in enumerate(["one", "two", "three.", "four", "five", "six."])]
    words.append(Word(6, " seven.", 5.0, 5.3))  # long pause before this word -> silence trim
    spec = ClipSpec(str(source_video), str(source_video), 1280, 720, 30.0, 0.4, 5.5, words, [], [(0.5, 5.3)],
                    hook_text="Four five six")
    tl, report = build_timeline(spec, AppSettings(), None)
    assert len(tl.ranges) == 2 and report["trimmed_s"] > 0.5
    assert [w.text for w in tl.captions.words][-1] == "seven."
    assert tl.captions.words[-1].end <= tl.duration + 1e-6
    assert tl.overlays and tl.overlays[0].text == "Four five six"
