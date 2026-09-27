import itertools

import numpy as np

from shortforge.core.config import SafeAreaSettings
from shortforge.engines.captions.ass import ass_color, ass_time, build_ass, rounded_rect
from shortforge.engines.captions.emphasis import select_emphasis
from shortforge.engines.captions.layout import (
    CapWord,
    LayoutConfig,
    check_overflow,
    layout_pages,
    max_text_width,
)
from shortforge.engines.captions.presets import builtin_presets, get_preset
from shortforge.engines.editing.timeline import CropKey, EditTimeline, SourceRange, ZoomEvent
from shortforge.engines.reframing.camera import (
    catmull_rom,
    clamp_center,
    ease_in_out,
    fill_gaps,
    jitter_score,
    plan_holds,
    render_path,
    spring_smooth,
)
from shortforge.engines.rendering.compositor import CropTrack, crop_rect, zoom_at


def test_all_presets_load() -> None:
    presets = builtin_presets()
    assert len(presets) == 13
    for name in ("Minimal", "Bold", "Energetic", "Podcast", "Documentary", "Tech", "Luxury", "Gaming", "Clean",
                 "Cinematic", "Impact", "Neon", "Modern"):
        assert presets[name].font_path.exists(), name


def test_semantic_emphasis() -> None:
    words = ["this", "graphics", "card", "is", "almost", "twice", "as", "fast"]
    emph = select_emphasis(words, keywords=["graphics card", "twice as fast"], ratio=0.5)
    chosen = {w for w, e in zip(words, emph, strict=False) if e}
    assert {"graphics", "card", "twice", "fast"} <= chosen
    assert "this" not in chosen and "is" not in chosen


def test_emphasis_never_picks_fillers() -> None:
    words = ["um", "like", "you", "know", "basically", "the", "thing"]
    assert not any(select_emphasis(words))


def _words(texts: list[str], start: float = 0.0) -> list[CapWord]:
    return [CapWord(t, start + i * 0.3, start + i * 0.3 + 0.25) for i, t in enumerate(texts)]


def test_layout_respects_safe_area_for_every_preset() -> None:
    cfg = LayoutConfig(1080, 1920)
    long_words = _words(["Supercalifragilisticexpialidocious", "incomprehensibilities", "notwithstanding", "everything"])
    for preset in builtin_presets().values():
        pages = layout_pages(long_words, preset, cfg, clip_duration=10)
        assert pages
        assert check_overflow(pages, cfg) == [], preset.name


def test_layout_breaks_on_sentence_end_and_pause() -> None:
    cfg = LayoutConfig(1080, 1920)
    words = [CapWord("Hello.", 0.0, 0.3), CapWord("New", 0.35, 0.6), CapWord("sentence", 0.65, 0.9),
             CapWord("after", 2.0, 2.3)]
    pages = layout_pages(words, get_preset("Bold"), cfg, clip_duration=5)
    assert [p.text for p in pages] == ["HELLO.", "NEW SENTENCE", "AFTER"]
    # pages never overlap in time
    for a, b in itertools.pairwise(pages):
        assert a.end <= b.start + 1e-6


def test_active_word_growth_never_overlaps_neighbours() -> None:
    cfg = LayoutConfig(1080, 1920)
    preset = get_preset("Bold")
    pages = layout_pages(_words(["THE", "PROGRAM", "A", "BIT", "MORE"]), preset, cfg, clip_duration=5)
    grow = preset.active_scale - 1
    checked = 0
    for page in pages:
        by_line: dict[int, list] = {}
        for w in page.words:
            by_line.setdefault(w.line, []).append(w)
        for ws in by_line.values():
            for a, b in itertools.pairwise(ws):
                # Only one word is active at a time: its popped glyph box must not reach the neighbour.
                assert a.x + a.width / 2 * (1 + grow) < b.x - b.width / 2
                assert b.x - b.width / 2 * (1 + grow) > a.x + a.width / 2
                checked += 1
    assert checked > 0


def test_safe_area_update_changes_max_width() -> None:
    narrow = LayoutConfig(1080, 1920, safe=SafeAreaSettings(left=0.2, right=0.2))
    assert max_text_width(narrow) < max_text_width(LayoutConfig(1080, 1920))


def test_ass_output() -> None:
    assert ass_color("#FF8000") == "&H000080FF"
    assert ass_color("#000000", 0.5) == "&H80000000"
    assert ass_time(3661.256) == "1:01:01.26"
    assert rounded_rect(100, 40, 10).startswith("m 10.0 0 l 90.0 0")
    content, pages = build_ass(_words(["one", "two", "three", "four", "five"]), get_preset("Tech"), LayoutConfig(),
                               clip_duration=3)
    assert "[Events]" in content and "Dialogue:" in content and pages
    assert "\\p1" in content  # box highlight drawing present for Tech preset


def test_ease_and_catmull() -> None:
    assert ease_in_out(0) == 0 and ease_in_out(1) == 1 and abs(ease_in_out(0.5) - 0.5) < 1e-9
    keys_t, keys_v = [0, 1, 2, 3], [0.2, 0.2, 0.8, 0.8]
    vals = [catmull_rom(keys_t, keys_v, t) for t in np.linspace(0, 3, 61)]
    assert min(vals) >= 0.2 - 1e-9 and max(vals) <= 0.8 + 1e-9  # no overshoot


def test_camera_holds_through_small_motion_and_moves_on_real_change() -> None:
    t = np.arange(0, 10, 0.2)
    rng = np.random.default_rng(0)
    x = np.where(t < 5, 0.40, 0.62) + rng.normal(0, 0.01, len(t))
    holds = plan_holds(t, x, deadzone=0.05, min_hold=0.6)
    assert len(holds) == 2
    path = render_path(holds, t, cut_threshold=0.5)
    smooth = spring_smooth(path, 0.2)
    assert abs(smooth[5] - 0.40) < 0.02 and abs(smooth[-1] - 0.62) < 0.02
    # far less jitter than the raw detections
    assert jitter_score(smooth, 0.2) < jitter_score(x, 0.2) / 5


def test_gap_fill_and_clamp() -> None:
    np.testing.assert_allclose(fill_gaps([None, 0.2, None, 0.6, None]), [0.2, 0.2, 0.4, 0.6, 0.6])
    assert clamp_center(0.01, 0.3) == 0.15 and clamp_center(0.99, 0.3) == 0.85


def test_crop_rect_and_interpolation() -> None:
    x0, _y0, w, h = crop_rect(1920, 1080, 0.5, 0.5, 1.0, 9 / 16)
    assert abs(w - 607.5) < 0.01 and h == 1080 and abs(x0 - (960 - 303.75)) < 0.01
    x0, _, _, _ = crop_rect(1920, 1080, 0.0, 0.5, 1.0, 9 / 16)
    assert x0 == 0.0  # clamped inside the frame
    track = CropTrack.from_keys([CropKey(t=0, cx=0.3), CropKey(t=1, cx=0.3), CropKey(t=2, cx=0.7)])
    assert abs(track.at(0.5)[0] - 0.3) < 1e-6 and 0.3 <= track.at(1.5)[0] <= 0.7


def test_zoom_events_ease() -> None:
    ev = [ZoomEvent(start=1, end=3, scale=1.1, ease_in=0.5, ease_out=0.5)]
    assert zoom_at(ev, 0.5) == 1.0 and abs(zoom_at(ev, 2.0) - 1.1) < 1e-9 and 1.0 < zoom_at(ev, 1.2) < 1.1


def test_timeline_mapping() -> None:
    tl = EditTimeline(source_path="x", source_width=1920, source_height=1080,
                      ranges=[SourceRange(start=10, end=12), SourceRange(start=15, end=16)])
    assert tl.duration == 3
    assert tl.source_to_output(11) == 1 and tl.source_to_output(13) is None and tl.source_to_output(15.5) == 2.5
    assert tl.output_to_source(2.5) == 15.5
    assert tl.cut_points() == [2]


def test_flash_pages_are_merged() -> None:
    cfg = LayoutConfig(1080, 1920)
    words = [CapWord("we", 7.5, 7.8), CapWord("understand", 8.0, 8.04), CapWord("that.", 8.04, 8.4)]
    pages = layout_pages(words, get_preset("Bold"), cfg, clip_duration=10)
    assert all(p.end - p.start >= 0.3 for p in pages)


def test_punch_in_density_cap() -> None:
    from shortforge.engines.reframing.planner import ReframeConfig, plan_punch_ins

    tl = EditTimeline(source_path="x", source_width=1920, source_height=1080,
                      ranges=[SourceRange(start=0, end=4), SourceRange(start=5, end=9), SourceRange(start=10, end=16)])
    events = plan_punch_ins(tl, [2.0, 7.0, 12.0], ReframeConfig(), 1080)
    assert len(events) <= max(1, int(tl.duration / 8))
    for a, b in itertools.pairwise(events):
        assert b.start >= a.end + 3.0
