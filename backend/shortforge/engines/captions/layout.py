"""Caption pagination, line breaking and exact word placement.

Text is measured with the real font file (FreeType via Pillow) using the same size semantics as
libass (font size = winAscent + winDescent), so line widths computed here match the rendered output
and overflow can be ruled out before rendering.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

from shortforge.core.config import SafeAreaSettings
from shortforge.engines.captions.presets import CaptionPreset

try:
    from fontTools.ttLib import TTFont
except ImportError:  # pragma: no cover
    TTFont = None


@dataclass
class CapWord:
    text: str
    start: float
    end: float
    emphasis: bool = False
    speaker: int | None = None


@dataclass
class PlacedWord:
    text: str
    start: float
    end: float
    x: float  # centre x (px)
    y: float  # centre y (px)
    width: float
    emphasis: bool
    speaker: int | None
    line: int


@dataclass
class Page:
    start: float
    end: float
    words: list[PlacedWord] = field(default_factory=list)
    lines: list[tuple[float, float, float]] = field(default_factory=list)  # (x_left, x_right, y_center)
    font_scale: float = 1.0

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


@lru_cache(maxsize=64)
def libass_em_ratio(font_path: str) -> float:
    """em size / ASS font size, i.e. unitsPerEm / (usWinAscent + usWinDescent)."""
    if TTFont is None:
        return 0.72
    font = TTFont(font_path, lazy=True)
    os2 = font["OS/2"]
    total = os2.usWinAscent + os2.usWinDescent
    return font["head"].unitsPerEm / total if total else 0.72


@lru_cache(maxsize=256)
def _pil_font(font_path: str, px: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(font_path, px)


class TextMeasurer:
    def __init__(self, font_path: Path, em_px: float, letter_spacing: float = 0.0, outline: float = 0.0) -> None:
        self.font_path = str(font_path)
        self.em_px = em_px
        self.letter_spacing = letter_spacing
        self.outline = outline

    @property
    def ass_size(self) -> float:
        return self.em_px / libass_em_ratio(self.font_path)

    def width(self, text: str, scale: float = 1.0) -> float:
        px = max(4, round(self.em_px * scale))
        font = _pil_font(self.font_path, px)
        w = font.getlength(text)
        w += max(0, len(text) - 1) * self.letter_spacing * scale
        return w

    def space(self, scale: float = 1.0) -> float:
        return _pil_font(self.font_path, max(4, round(self.em_px * scale))).getlength(" ") + self.letter_spacing

    def line_height(self, scale: float = 1.0) -> float:
        return self.em_px * scale * 1.18


@dataclass
class LayoutConfig:
    width: int = 1080
    height: int = 1920
    safe: SafeAreaSettings = field(default_factory=SafeAreaSettings)
    max_page_duration: float = 2.8
    gap_break: float = 0.5
    linger: float = 0.35


def max_text_width(cfg: LayoutConfig) -> float:
    """Captions are centred; both edges must stay inside the safe margins."""
    left = cfg.safe.left * cfg.width
    right = cfg.width * (1 - cfg.safe.right)
    centre = cfg.width / 2
    return 2 * min(centre - left, right - centre)


def transform(text: str, preset: CaptionPreset) -> str:
    text = text.strip()
    return text.upper() if preset.uppercase else text


def pop_growth(preset: CaptionPreset) -> float:
    """Fractional growth of a word while highlighted/emphasised (it scales around its centre)."""
    active = preset.active_scale if preset.highlight_mode in ("color", "karaoke") else 1.0
    return max(0.0, active * max(1.0, preset.emphasis_scale) - 1.0)


POP_RESERVE = 0.7
"""Share of the active word's half-growth reserved as extra spacing. The normal word space absorbs
the rest, so a popped word never touches its neighbour while idle pages don't look loose."""


def pair_gap(w_a: float, w_b: float, space: float, grow: float) -> float:
    """Gap between neighbours so a scaled-up word never collides with the next one."""
    return space + POP_RESERVE * grow * max(w_a, w_b) / 2


def line_width(widths: list[float], idxs: list[int], space: float, grow: float) -> float:
    if not idxs:
        return 0.0
    total = sum(widths[i] for i in idxs)
    total += sum(pair_gap(widths[a], widths[b], space, grow) for a, b in itertools.pairwise(idxs))
    # Edge words also grow outward when active.
    return total + grow * (widths[idxs[0]] + widths[idxs[-1]]) / 2


def _split_lines(widths: list[float], space: float, max_w: float, max_lines: int,
                 grow: float = 0.0) -> list[list[int]] | None:
    """Balanced line breaking: minimise the widest line (<= max_lines lines), or None if impossible."""
    n = len(widths)

    def line_w(a: int, b: int) -> float:
        return line_width(widths, list(range(a, b)), space, grow)

    if line_w(0, n) <= max_w:
        return [list(range(n))]
    if max_lines >= 2:
        best, best_w = None, float("inf")
        for k in range(1, n):
            w = max(line_w(0, k), line_w(k, n))
            if w <= max_w and w < best_w:
                best, best_w = k, w
        if best is not None:
            return [list(range(best)), list(range(best, n))]
    if max_lines >= 3:
        best3, best_w = None, float("inf")
        for a in range(1, n - 1):
            for b in range(a + 1, n):
                w = max(line_w(0, a), line_w(a, b), line_w(b, n))
                if w <= max_w and w < best_w:
                    best3, best_w = (a, b), w
        if best3:
            a, b = best3
            return [list(range(a)), list(range(a, b)), list(range(b, n))]
    return None


def paginate(words: list[CapWord], preset: CaptionPreset, cfg: LayoutConfig,
             max_words: int | None = None) -> list[list[CapWord]]:
    """Group words into pages that fit, respecting pauses and sentence ends."""
    measurer = TextMeasurer(preset.font_path, preset.font_size, preset.letter_spacing, preset.outline_width)
    max_w = max_text_width(cfg) - 2 * preset.outline_width
    limit = max_words or preset.max_words
    pages: list[list[CapWord]] = []
    cur: list[CapWord] = []
    for w in words:
        if cur:
            gap = w.start - cur[-1].end
            prev_text = cur[-1].text.strip()
            sentence_end = prev_text.endswith((".", "!", "?", "…"))
            clause_end = prev_text.endswith((",", ";", ":")) and len(cur) >= 2
            too_long = w.end - cur[0].start > cfg.max_page_duration
            candidate = [transform(x.text, preset) for x in [*cur, w]]
            widths = [measurer.width(t) for t in candidate]
            fits = _split_lines(widths, measurer.space(), max_w, preset.max_lines, pop_growth(preset)) is not None
            if len(cur) >= limit or gap > cfg.gap_break or sentence_end or clause_end or too_long or not fits:
                pages.append(cur)
                cur = []
        cur.append(w)
    if cur:
        pages.append(cur)
    return pages


def layout_pages(words: list[CapWord], preset: CaptionPreset, cfg: LayoutConfig,
                 clip_duration: float | None = None, max_words: int | None = None,
                 vertical_position: float | None = None) -> list[Page]:
    pages_words = paginate(words, preset, cfg, max_words)
    max_w = max_text_width(cfg) - 2 * preset.outline_width
    top_limit = cfg.safe.top * cfg.height
    bottom_limit = cfg.height * (1 - cfg.safe.bottom)
    centre_y = (vertical_position if vertical_position is not None else preset.position) * cfg.height
    out: list[Page] = []
    for pi, pw in enumerate(pages_words):
        texts = [transform(w.text, preset) for w in pw]
        grow = pop_growth(preset)
        scale = 1.0
        while True:
            m = TextMeasurer(preset.font_path, preset.font_size * scale, preset.letter_spacing * scale,
                             preset.outline_width)
            widths = [m.width(t) for t in texts]
            lines = _split_lines(widths, m.space(), max_w, preset.max_lines, grow)
            if lines is not None or scale < 0.25:
                break
            scale *= 0.92  # a single very long word: shrink this page only (never overflow)
        if lines is None:
            lines = [list(range(len(texts)))]
        lh = m.line_height() * preset.line_spacing
        block_h = lh * len(lines)
        # Keep the whole block inside the vertical safe area.
        cy = min(max(centre_y, top_limit + block_h / 2), bottom_limit - block_h / 2)
        start = pw[0].start
        nxt = pages_words[pi + 1][0].start if pi + 1 < len(pages_words) else None
        end = pw[-1].end + cfg.linger
        if nxt is not None:
            end = nxt if nxt - pw[-1].end < 0.6 else min(end, nxt)
        if clip_duration is not None:
            end = min(end, clip_duration)
        page = Page(start=start, end=max(end, pw[-1].end), font_scale=scale)
        space = m.space()
        for li, idxs in enumerate(lines):
            edge = grow * (widths[idxs[0]] + widths[idxs[-1]]) / 2
            lw = line_width(widths, idxs, space, grow) - edge
            x = cfg.width / 2 - lw / 2
            y = cy - block_h / 2 + lh * (li + 0.5)
            page.lines.append((x, x + lw, y))
            for k, i in enumerate(idxs):
                w = pw[i]
                page.words.append(PlacedWord(texts[i], w.start, w.end, x + widths[i] / 2, y, widths[i], w.emphasis,
                                             w.speaker, li))
                if k + 1 < len(idxs):
                    x += widths[i] + pair_gap(widths[i], widths[idxs[k + 1]], space, grow)
        out.append(page)
    return out


def check_overflow(pages: list[Page], cfg: LayoutConfig) -> list[str]:
    """Return human-readable problems if any placed word leaves the safe area."""
    issues = []
    left = cfg.safe.left * cfg.width - 0.5
    right = cfg.width * (1 - cfg.safe.right) + 0.5
    top = cfg.safe.top * cfg.height
    bottom = cfg.height * (1 - cfg.safe.bottom)
    for p in pages:
        for w in p.words:
            if w.x - w.width / 2 < left or w.x + w.width / 2 > right:
                issues.append(f"'{w.text}' at {w.start:.1f}s crosses the horizontal safe area")
            if not top <= w.y <= bottom:
                issues.append(f"'{w.text}' at {w.start:.1f}s is outside the vertical safe area")
    return issues
