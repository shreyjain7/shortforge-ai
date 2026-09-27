import { describe, expect, it } from "vitest";
import { cropAt, cutPoints, deleteRange, duration, outToSrc, pinCrop, splitAt, srcToOut, trimRange, withRanges, zoomAt } from "./timeline";
import type { EditTimeline } from "./types";

function tl(): EditTimeline {
  return {
    schema_version: 1, source_path: "x", source_width: 1920, source_height: 1080, fps: 30, width: 1080, height: 1920,
    ranges: [{ start: 10, end: 14 }, { start: 20, end: 22 }],
    layouts: [{ start: 0, end: 6, layout: "crop", secondary: [], note: null }],
    crop: [{ t: 0, cx: 0.4, cy: 0.5, zoom: 1 }, { t: 6, cx: 0.6, cy: 0.5, zoom: 1 }],
    zooms: [{ start: 4.5, end: 5.5, scale: 1.1, ease_in: 0.2, ease_out: 0.2, reason: null }],
    captions: { enabled: true, preset: "Bold", max_words: null, vertical_position: null, overrides: {},
      words: [
        { text: "a", start: 1, end: 1.3, emphasis: false, speaker: null, src_start: 11, src_end: 11.3 },
        { text: "b", start: 4.5, end: 4.8, emphasis: true, speaker: null, src_start: 20.5, src_end: 20.8 },
      ] },
    overlays: [], broll: [], music: null,
    audio: { target_lufs: -14, true_peak: -1.5, fade_ms: 40, compressor: false, eq: false, denoise: false, gain_db: 0 },
    enhance: { sharpen: false, vignette: false, contrast: false, color: false, denoise: false },
    safe_area: { top: 0.08, bottom: 0.22, left: 0.06, right: 0.14 }, speed: 1, notes: [],
  };
}

describe("time mapping", () => {
  it("maps between source and output time", () => {
    const r = tl().ranges;
    expect(duration(r)).toBe(6);
    expect(outToSrc(r, 1)).toBe(11);
    expect(outToSrc(r, 5)).toBe(21);
    expect(srcToOut(r, 21)).toBe(5);
    expect(srcToOut(r, 16)).toBeNull();
    expect(cutPoints(r)).toEqual([4]);
  });
});

describe("edits", () => {
  it("deleting a range drops its captions and re-times the rest", () => {
    const out = deleteRange(tl(), 0);
    expect(out.ranges).toEqual([{ start: 20, end: 22 }]);
    expect(out.captions.words.map((w) => w.text)).toEqual(["b"]);
    expect(out.captions.words[0].start).toBeCloseTo(0.5);
    expect(out.zooms[0].start).toBeCloseTo(0.5);
  });

  it("trimming the start of a range shifts later words", () => {
    const out = trimRange(tl(), 0, "start", 10.5, 100);
    expect(duration(out.ranges)).toBeCloseTo(5.5);
    expect(out.captions.words[0].start).toBeCloseTo(0.5);
    expect(out.captions.words[1].start).toBeCloseTo(4.0);
  });

  it("split keeps all material", () => {
    const out = splitAt(tl(), 2);
    expect(out.ranges.length).toBe(3);
    expect(duration(out.ranges)).toBe(6);
  });

  it("crop interpolation, pinning and zoom easing", () => {
    expect(cropAt(tl(), 3).cx).toBeCloseTo(0.5);
    const pinned = pinCrop(tl(), 3, 0.2, 1);
    expect(cropAt(pinned, 3.5).cx).toBeCloseTo(0.2);
    expect(zoomAt(tl(), 5)).toBeCloseTo(1.1);
    expect(zoomAt(tl(), 1)).toBe(1);
  });

  it("withRanges tolerates removing everything but one range", () => {
    const out = withRanges(tl(), [{ start: 20, end: 22 }]);
    expect(out.layouts[0].start).toBe(0);
    expect(out.layouts[out.layouts.length - 1].end).toBeCloseTo(2);
  });
});
