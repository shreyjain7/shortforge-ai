/**
 * Pure, non-destructive edit operations on an EditTimeline.
 *
 * Source ranges define the cut. Everything else lives on the output timeline, so any change to the
 * ranges re-maps items through source time: output -> source (old cut) -> output (new cut).
 * Items that fall into removed material are dropped.
 */
import type { CaptionWord, CropKey, EditTimeline } from "./types";

export type Range = { start: number; end: number };

export function duration(ranges: Range[]): number {
  return ranges.reduce((acc, r) => acc + (r.end - r.start), 0);
}

export function outToSrc(ranges: Range[], t: number): number {
  let acc = 0;
  for (const r of ranges) {
    const d = r.end - r.start;
    if (t <= acc + d) return r.start + Math.max(0, t - acc);
    acc += d;
  }
  return ranges.length ? ranges[ranges.length - 1].end : 0;
}

export function srcToOut(ranges: Range[], t: number): number | null {
  let acc = 0;
  for (const r of ranges) {
    if (t >= r.start - 1e-6 && t <= r.end + 1e-6) return acc + Math.min(Math.max(t - r.start, 0), r.end - r.start);
    acc += r.end - r.start;
  }
  return null;
}

export function cutPoints(ranges: Range[]): number[] {
  const out: number[] = [];
  let acc = 0;
  for (const r of ranges.slice(0, -1)) {
    acc += r.end - r.start;
    out.push(acc);
  }
  return out;
}

function remapTime(oldR: Range[], newR: Range[], t: number): number | null {
  return srcToOut(newR, outToSrc(oldR, t));
}

/** Replace the cut and re-time every dependent item. */
export function withRanges(tl: EditTimeline, ranges: Range[]): EditTimeline {
  const clean = ranges
    .map((r) => ({ start: +r.start.toFixed(3), end: +r.end.toFixed(3) }))
    .filter((r) => r.end - r.start > 0.05)
    .sort((a, b) => a.start - b.start);
  const old = tl.ranges;
  const total = duration(clean);
  const map = (t: number) => remapTime(old, clean, t);

  const words: CaptionWord[] = [];
  for (const w of tl.captions.words) {
    const s = w.src_start != null ? srcToOut(clean, w.src_start) : map(w.start);
    const e = w.src_end != null ? srcToOut(clean, w.src_end) : map(w.end);
    if (s === null) continue;
    words.push({ ...w, start: +s.toFixed(3), end: +Math.min(total, Math.max(s + 0.05, e ?? s + 0.2)).toFixed(3) });
  }
  const crop: CropKey[] = [];
  for (const k of tl.crop) {
    const t = map(k.t);
    if (t !== null) crop.push({ ...k, t: +t.toFixed(3) });
  }
  if (!crop.length) crop.push({ t: 0, cx: 0.5, cy: 0.5, zoom: 1 });
  const zooms = tl.zooms
    .map((z) => ({ ...z, start: map(z.start), end: map(z.end) }))
    .filter((z): z is typeof z & { start: number; end: number } => z.start !== null && z.end !== null && z.end - z.start > 0.2);
  const layouts = tl.layouts
    .map((l) => ({ ...l, start: map(l.start) ?? 0, end: map(l.end) ?? total }))
    .filter((l) => l.end - l.start > 0.05);
  if (layouts.length) {
    layouts[0].start = 0;
    layouts[layouts.length - 1].end = total;
  }
  return {
    ...tl,
    ranges: clean,
    captions: { ...tl.captions, words },
    crop,
    zooms,
    layouts: layouts.length ? layouts : [{ start: 0, end: total, layout: "crop", secondary: [], note: null }],
    overlays: tl.overlays.map((o) => ({ ...o, end: Math.min(o.end, total) })).filter((o) => o.start < total),
    broll: tl.broll.filter((b) => b.start < total).map((b) => ({ ...b, end: Math.min(b.end, total) })),
  };
}

/** Split the range under output time t into two ranges (no material removed). */
export function splitAt(tl: EditTimeline, t: number): EditTimeline {
  const src = outToSrc(tl.ranges, t);
  const ranges: Range[] = [];
  for (const r of tl.ranges) {
    if (src > r.start + 0.1 && src < r.end - 0.1) {
      ranges.push({ start: r.start, end: src }, { start: src, end: r.end });
    } else ranges.push({ ...r });
  }
  return { ...tl, ranges }; // no re-timing needed: the cut material is identical
}

export function deleteRange(tl: EditTimeline, index: number): EditTimeline {
  if (tl.ranges.length <= 1) return tl;
  return withRanges(tl, tl.ranges.filter((_, i) => i !== index));
}

export function trimRange(tl: EditTimeline, index: number, edge: "start" | "end", srcTime: number, sourceDuration: number): EditTimeline {
  const ranges = tl.ranges.map((r) => ({ ...r }));
  const r = ranges[index];
  const prev = ranges[index - 1];
  const next = ranges[index + 1];
  if (edge === "start") r.start = Math.min(Math.max(srcTime, prev ? prev.end : 0), r.end - 0.2);
  else r.end = Math.max(Math.min(srcTime, next ? next.start : sourceDuration), r.start + 0.2);
  return withRanges(tl, ranges);
}

export function cropAt(tl: EditTimeline, t: number): CropKey {
  const keys = [...tl.crop].sort((a, b) => a.t - b.t);
  if (!keys.length) return { t, cx: 0.5, cy: 0.5, zoom: 1 };
  if (t <= keys[0].t) return keys[0];
  for (let i = 0; i < keys.length - 1; i++) {
    const a = keys[i];
    const b = keys[i + 1];
    if (t >= a.t && t <= b.t) {
      const u = b.t > a.t ? (t - a.t) / (b.t - a.t) : 0;
      return { t, cx: a.cx + (b.cx - a.cx) * u, cy: a.cy + (b.cy - a.cy) * u, zoom: a.zoom + (b.zoom - a.zoom) * u };
    }
  }
  return keys[keys.length - 1];
}

/** Manually pin the camera: replaces automatic keys within ±hold seconds with a steady position. */
export function pinCrop(tl: EditTimeline, t: number, cx: number, hold = 1.5): EditTimeline {
  const start = Math.max(0, t - 0.01);
  const end = Math.min(duration(tl.ranges), t + hold);
  const kept = tl.crop.filter((k) => k.t < start - 0.2 || k.t > end + 0.2);
  const cy = cropAt(tl, t).cy;
  const keys: CropKey[] = [...kept, { t: start, cx, cy, zoom: 1 }, { t: end, cx, cy, zoom: 1 }];
  return { ...tl, crop: keys.sort((a, b) => a.t - b.t) };
}

export function zoomAt(tl: EditTimeline, t: number): number {
  let z = 1;
  for (const e of tl.zooms) {
    if (t < e.start || t > e.end) continue;
    let f = 1;
    if (e.ease_in > 0 && t < e.start + e.ease_in) f = smooth((t - e.start) / e.ease_in);
    else if (e.ease_out > 0 && t > e.end - e.ease_out) f = smooth((e.end - t) / e.ease_out);
    z *= 1 + (e.scale - 1) * f;
  }
  return z;
}

function smooth(u: number): number {
  const x = Math.min(1, Math.max(0, u));
  return x * x * x * (x * (x * 6 - 15) + 10);
}

export function layoutAt(tl: EditTimeline, t: number) {
  return tl.layouts.find((l) => t >= l.start && t < l.end) ?? tl.layouts[tl.layouts.length - 1];
}
