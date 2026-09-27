import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import {
  ArrowLeft, Clapperboard, Crosshair, Film, History, Image as ImageIcon, Music, Pause, Play, Plus, Redo2, Save, Scissors, SkipBack,
  Trash2, Type, Undo2, Wand2, ZoomIn,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Badge, Button, Empty, FieldRow, PageHeader, Segmented, Skeleton, Toggle } from "../components/ui";
import { API, api, mediaUrl } from "../lib/api";
import { useToast } from "../lib/events";
import { fmtDuration, fmtTimecode } from "../lib/format";
import {
  cropAt, cutPoints, deleteRange, duration, layoutAt, outToSrc, pinCrop, splitAt, srcToOut, trimRange, zoomAt,
} from "../lib/timeline";
import type { CaptionPreset, EditTimeline } from "../lib/types";

type Sel =
  | { kind: "range"; index: number }
  | { kind: "word"; index: number }
  | { kind: "zoom"; index: number }
  | { kind: "overlay"; index: number }
  | { kind: "broll"; index: number }
  | { kind: "layout"; index: number }
  | null;

const TRACK_H = 34;
const loadedFonts = new Set<string>();

function useFont(preset?: CaptionPreset) {
  useEffect(() => {
    if (!preset || loadedFonts.has(preset.font_file)) return;
    const face = new FontFace(`sf-${preset.font_file}`, `url(${API}/templates/fonts/${encodeURIComponent(preset.font_file)})`);
    face.load().then((f) => { document.fonts.add(f); loadedFonts.add(preset.font_file); }).catch(() => undefined);
  }, [preset]);
}

/** Real-time approximation of the final render: virtual camera on the proxy + captions overlay. */
function LivePreview({ tl, proxy, t, preset, videoRef, showSafe }: {
  tl: EditTimeline; proxy: string | null; t: number; preset?: CaptionPreset; videoRef: React.RefObject<HTMLVideoElement | null>; showSafe: boolean;
}) {
  const W = 320;
  const H = (W * 16) / 9;
  const layout = layoutAt(tl, t);
  const key = cropAt(tl, t);
  const zoom = Math.max(1, key.zoom * zoomAt(tl, t));
  const srcAspect = tl.source_width / tl.source_height;
  let style: React.CSSProperties;
  if (layout?.layout === "fit") {
    const h = W / srcAspect;
    style = { width: W, height: h, left: 0, top: H * 0.42 - h / 2 };
  } else {
    // Crop window as a fraction of the source frame (same maths as the renderer's crop_rect).
    const landscape = srcAspect > 9 / 16;
    const cropW = landscape ? (9 / 16) / srcAspect / zoom : 1 / zoom;
    const cropH = landscape ? 1 / zoom : (cropW * srcAspect) / (9 / 16);
    const vw = W / cropW;
    const vh = vw / srcAspect;
    const x0 = Math.min(Math.max(key.cx - cropW / 2, 0), 1 - cropW);
    const cyAdj = zoom > 1 ? key.cy + (0.5 - 0.38) * cropH : 0.5;
    const y0 = Math.min(Math.max(cyAdj - cropH / 2, 0), Math.max(0, 1 - cropH));
    style = { width: vw, height: vh, left: -x0 * vw, top: -y0 * vh };
  }
  // Captions: current page = words around t, chunked like the preset (live, pre-render approximation).
  const words = tl.captions.enabled ? tl.captions.words : [];
  const per = tl.captions.max_words ?? preset?.max_words ?? 3;
  const idx = words.findIndex((w) => t >= w.start - 0.05 && t < w.end + 0.35);
  const pageStart = idx >= 0 ? Math.floor(idx / per) * per : -1;
  const page = pageStart >= 0 ? words.slice(pageStart, pageStart + per) : [];
  const scale = W / tl.width;
  const pos = tl.captions.vertical_position ?? preset?.position ?? 0.68;
  const hook = tl.overlays.find((o) => o.kind === "hook" && t >= o.start && t < o.end);
  const broll = tl.broll.find((b) => t >= b.start && t < b.end);
  return (
    <div className="phone" style={{ width: W, height: H }}>
      <div style={{ position: "absolute", inset: 0, background: layout?.layout === "fit" ? "radial-gradient(circle at 50% 40%, #1f2033, #07070b)" : "#000" }} />
      {proxy ? <video ref={videoRef} src={mediaUrl(proxy)} preload="auto" playsInline style={{ position: "absolute", ...style, objectFit: "fill" }}
        onLoadedMetadata={(e) => { e.currentTarget.currentTime = outToSrc(tl.ranges, t); }} />
        : <div className="center faint" style={{ height: "100%" }}>No proxy</div>}
      {layout?.layout === "split" && <div className="badge" style={{ position: "absolute", top: 8, left: 8 }}>Split layout · rendered top/bottom</div>}
      {broll && <div className="center" style={{ position: "absolute", inset: broll.mode === "full" ? 0 : "6% 6% auto auto", width: broll.mode === "full" ? "100%" : "34%",
        height: broll.mode === "full" ? "100%" : "20%", background: "rgba(34,211,238,.18)", border: "1px dashed var(--accent-2)", color: "#cffafe", fontSize: 11 }}>
        <ImageIcon size={16} /> B-roll: {broll.label ?? broll.path.split(/[\\/]/).pop()}</div>}
      {hook && <div style={{ position: "absolute", left: "8%", right: "8%", top: `${(tl.safe_area.top * 100) + 4}%`, textAlign: "center" }}>
        <span style={{ background: "#fff", color: "#111", fontWeight: 800, padding: "4px 10px", borderRadius: 8, fontSize: 14, fontFamily: "Montserrat, var(--font)" }}>{hook.text}</span></div>}
      {page.length > 0 && preset && (
        <div style={{ position: "absolute", left: `${tl.safe_area.left * 100}%`, right: `${tl.safe_area.right * 100}%`, top: `${pos * 100}%`,
          transform: "translateY(-50%)", textAlign: "center", lineHeight: 1.1, fontFamily: `"sf-${preset.font_file}", var(--font)`,
          fontSize: preset.font_size * scale, textTransform: preset.uppercase ? "uppercase" : "none" }}>
          {page.map((w, i) => {
            const active = t >= w.start && (i + 1 < page.length ? t < page[i + 1].start : true);
            const color = active && preset.highlight_mode !== "none" ? preset.highlight_color : w.emphasis ? preset.emphasis_color : preset.primary_color;
            return (
              <span key={i} style={{ color, margin: "0 0.12em", display: "inline-block", transform: active ? "scale(1.08)" : undefined,
                WebkitTextStroke: preset.outline_width ? `${Math.max(1, preset.outline_width * scale)}px ${preset.outline_color}` : undefined,
                paintOrder: "stroke fill", textShadow: "0 2px 4px rgba(0,0,0,.5)",
                background: active && preset.highlight_mode === "box" ? String(preset.highlight_box_color ?? "#7c3aed") : undefined,
                borderRadius: 4, padding: active && preset.highlight_mode === "box" ? "0 4px" : undefined }}>{w.text}</span>
            );
          })}
        </div>
      )}
      {showSafe && (
        <div className="safe-overlay">
          <div className="zone" style={{ left: 0, right: 0, top: 0, height: `${tl.safe_area.top * 100}%` }} />
          <div className="zone" style={{ left: 0, right: 0, bottom: 0, height: `${tl.safe_area.bottom * 100}%` }} />
          <div className="zone" style={{ right: 0, top: `${tl.safe_area.top * 100}%`, bottom: `${tl.safe_area.bottom * 100}%`, width: `${tl.safe_area.right * 100}%` }} />
        </div>
      )}
    </div>
  );
}

function Picker() {
  const nav = useNavigate();
  const { data } = useQuery({ queryKey: ["shorts", "editor-picker"], queryFn: () => api.shorts({ limit: 60 }) });
  return (
    <div className="page">
      <PageHeader title="Editor" subtitle="Choose a Short to refine. Every change is saved as a new version — nothing is destructive." />
      {data?.items.length === 0 && <div className="card"><Empty icon={<Clapperboard size={22} />} title="Nothing to edit yet">Generate a Short first.</Empty></div>}
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))", gap: 16 }}>
        {data?.items.map((s) => (
          <div key={s.id} style={{ cursor: "pointer" }} onClick={() => nav(`/editor/${s.id}`)}>
            <div className="vthumb">{s.cover_url && <img src={mediaUrl(s.cover_url)} />}</div>
            <div className="small strong clamp-2" style={{ marginTop: 6 }}>{s.title}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function Editor() {
  const { id } = useParams();
  if (!id) return <Picker />;
  return <EditorInner sid={Number(id)} />;
}

function EditorInner({ sid }: { sid: number }) {
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const { data: short } = useQuery({ queryKey: ["short", sid], queryFn: () => api.short(sid) });
  const { data: video } = useQuery({ queryKey: ["video", short?.video_id], queryFn: () => api.video(short!.video_id!), enabled: !!short?.video_id });
  const { data: presets } = useQuery({ queryKey: ["presets"], queryFn: api.captionPresets });
  const [tl, setTl] = useState<EditTimeline | null>(null);
  const [history, setHistory] = useState<EditTimeline[]>([]);
  const [future, setFuture] = useState<EditTimeline[]>([]);
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [sel, setSel] = useState<Sel>(null);
  const [pps, setPps] = useState(24); // pixels per second
  const [panel, setPanel] = useState<"captions" | "framing" | "audio" | "look">("captions");
  const [showSafe, setShowSafe] = useState(true);
  const videoRef = useRef<HTMLVideoElement>(null);
  const loadedFor = useRef<number | null>(null);

  useEffect(() => {
    if (short?.timeline && loadedFor.current !== short.timeline_version) {
      setTl(short.timeline);
      loadedFor.current = short.timeline_version ?? null;
      setHistory([]);
      setFuture([]);
    }
  }, [short]);
  const preset = presets?.find((p) => p.name === tl?.captions.preset);
  useFont(preset);

  const commit = useCallback((next: EditTimeline) => {
    setTl((cur) => {
      if (cur) setHistory((h) => [...h.slice(-60), cur]);
      setFuture([]);
      return next;
    });
  }, []);
  const undo = () => { if (!history.length || !tl) return; setFuture((f) => [tl, ...f]); setTl(history[history.length - 1]); setHistory((h) => h.slice(0, -1)); };
  const redo = () => { if (!future.length || !tl) return; setHistory((h) => [...h, tl]); setTl(future[0]); setFuture((f) => f.slice(1)); };

  const total = tl ? duration(tl.ranges) : 0;

  // Output clock driven by the source proxy: skip removed material, stop at the end.
  useEffect(() => {
    let raf = 0;
    const tick = () => {
      const v = videoRef.current;
      if (v && tl && !v.paused) {
        const out = srcToOut(tl.ranges, v.currentTime);
        if (out === null) {
          const next = tl.ranges.find((r) => r.start > v.currentTime);
          if (next) v.currentTime = next.start; else { v.pause(); setPlaying(false); }
        } else {
          setT(out);
          if (out >= total - 0.03) { v.pause(); setPlaying(false); }
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [tl, total]);

  const seek = useCallback((out: number) => {
    const clamped = Math.max(0, Math.min(total, out));
    setT(clamped);
    if (videoRef.current && tl) videoRef.current.currentTime = outToSrc(tl.ranges, clamped);
  }, [tl, total]);
  const togglePlay = () => {
    const v = videoRef.current;
    if (!v || !tl) return;
    if (v.paused) {
      if (t >= total - 0.05) seek(0);
      else v.currentTime = outToSrc(tl.ranges, t);
      void v.play(); setPlaying(true);
    } else { v.pause(); setPlaying(false); }
  };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).tagName === "INPUT" || (e.target as HTMLElement).tagName === "TEXTAREA") return;
      if (e.code === "Space") { e.preventDefault(); togglePlay(); }
      if ((e.ctrlKey || e.metaKey) && e.key === "z") { e.preventDefault(); undo(); }
      if ((e.ctrlKey || e.metaKey) && e.key === "y") { e.preventDefault(); redo(); }
      if (e.key === "s" && !e.ctrlKey && tl) commit(splitAt(tl, t));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const save = useMutation({
    mutationFn: (render: boolean) => api.saveTimeline(sid, tl!, render),
    onSuccess: (r, render) => {
      toast({ title: `Saved version ${r.version}`, body: render ? "Rendering the new version…" : undefined, level: "success" });
      loadedFor.current = r.version;
      void qc.invalidateQueries({ queryKey: ["short", sid] });
    },
    onError: (e: Error) => toast({ title: "Save failed", body: e.message, level: "error" }),
  });
  const revert = useMutation({ mutationFn: (v: number) => api.revertTimeline(sid, v), onSuccess: () => { loadedFor.current = null; void qc.invalidateQueries({ queryKey: ["short", sid] }); } });

  const cuts = useMemo(() => (tl ? cutPoints(tl.ranges) : []), [tl]);
  if (!short || !tl) return <div className="page"><Skeleton h={600} /></div>;
  const width = Math.max(600, total * pps);
  const px = (s: number) => s * pps;
  const upd = (patch: Partial<EditTimeline>) => commit({ ...tl, ...patch });

  const selWord = sel?.kind === "word" ? tl.captions.words[sel.index] : null;
  const currentLayoutIdx = tl.layouts.findIndex((l) => t >= l.start && t < l.end);

  return (
    <div className="page" style={{ maxWidth: "none" }}>
      <div className="row" style={{ marginBottom: 12, gap: 8 }}>
        <Button variant="ghost" size="sm" onClick={() => nav(`/shorts/${sid}`)}><ArrowLeft size={14} /> {short.title.slice(0, 60)}</Button>
        <Badge>v{short.timeline_version}</Badge>
        {history.length > 0 && <Badge color="yellow">unsaved changes</Badge>}
        <div className="right row" style={{ gap: 6 }}>
          <Button size="sm" variant="ghost" icon disabled={!history.length} onClick={undo} title="Undo (Ctrl+Z)"><Undo2 size={14} /></Button>
          <Button size="sm" variant="ghost" icon disabled={!future.length} onClick={redo} title="Redo (Ctrl+Y)"><Redo2 size={14} /></Button>
          <select className="select" style={{ width: 150, height: 28 }} value="" onChange={(e) => e.target.value && revert.mutate(Number(e.target.value))}>
            <option value="">Versions…</option>
            {short.versions?.map((v) => <option key={v.version} value={v.version}>v{v.version} · {v.origin}</option>)}
          </select>
          <Button size="sm" onClick={() => save.mutate(false)} loading={save.isPending && save.variables === false}><Save size={13} /> Save</Button>
          <Button size="sm" variant="primary" onClick={() => save.mutate(true)} loading={save.isPending && save.variables === true}><Wand2 size={13} /> Save & render</Button>
        </div>
      </div>

      <div className="grid" style={{ gridTemplateColumns: "auto minmax(0,1fr) 320px", gap: 18, alignItems: "start" }}>
        <div className="col" style={{ gap: 8 }}>
          <LivePreview tl={tl} proxy={video?.proxy_url ?? null} t={t} preset={preset} videoRef={videoRef} showSafe={showSafe} />
          <div className="row" style={{ gap: 6 }}>
            <Button size="sm" icon onClick={() => seek(0)}><SkipBack size={14} /></Button>
            <Button size="sm" icon variant="primary" onClick={togglePlay}>{playing ? <Pause size={14} /> : <Play size={14} />}</Button>
            <span className="mono small">{fmtTimecode(t)}</span><span className="tiny faint">/ {fmtDuration(total)}</span>
            <button className={clsx("chip right", showSafe && "on")} onClick={() => setShowSafe(!showSafe)}>Safe</button>
          </div>
          <div className="tiny faint" style={{ maxWidth: 320 }}>Live preview approximates the render. <span className="kbd">Space</span> play · <span className="kbd">S</span> split · <span className="kbd">Ctrl+Z</span> undo</div>
        </div>

        <div className="card" style={{ minWidth: 0 }}>
          <div className="card-header" style={{ flexWrap: "wrap", rowGap: 10 }}>
            <span className="card-title">Timeline</span>
            <span className="card-sub">{tl.ranges.length} segment{tl.ranges.length > 1 ? "s" : ""} · {tl.captions.words.length} words · {tl.zooms.length} punch-ins</span>
            <div className="row-wrap" style={{ gap: 6, width: "100%" }}>
              <Button size="sm" onClick={() => commit(splitAt(tl, t))}><Scissors size={13} /> Split</Button>
              <Button size="sm" onClick={() => upd({ zooms: [...tl.zooms, { start: t, end: Math.min(total, t + 2), scale: 1.08, ease_in: 0.35, ease_out: 0.35, reason: "manual" }].sort((a, b) => a.start - b.start) })}>
                <ZoomIn size={13} /> Punch-in</Button>
              <Button size="sm" onClick={() => upd({ overlays: [...tl.overlays, { text: "Your hook text", start: t, end: Math.min(total, t + 2.5), kind: "hook" }] })}><Type size={13} /> Text</Button>
              <Button size="sm" onClick={() => { const p = prompt("Full path to a B-roll video file"); if (p) upd({ broll: [...tl.broll, { path: p, start: t, end: Math.min(total, t + 2), source_start: 0, mode: "full", label: p.split(/[\\/]/).pop() ?? null }] }); }}>
                <ImageIcon size={13} /> B-roll</Button>
              {(tl.broll_suggestions?.length ?? 0) > 0 && (
                <div className="row-wrap" style={{ gap: 6 }}>
                  <span className="tiny faint">B-roll suggestions:</span>
                  {tl.broll_suggestions!.map((b, i) => (
                    <button key={i} className="chip" title={b.path} onClick={() => upd({ broll: [...tl.broll, b].sort((a, c) => a.start - c.start),
                      broll_suggestions: tl.broll_suggestions!.filter((_, k) => k !== i) })}><Plus size={12} /> {b.label ?? "clip"} @ {fmtDuration(b.start)}</button>
                  ))}
                </div>
              )}
              <div className="row right small faint" style={{ gap: 6 }}><ZoomIn size={12} />
                <input type="range" className="range" min={8} max={80} value={pps} onChange={(e) => setPps(Number(e.target.value))} style={{ width: 90 }} title="Zoom timeline" /></div>
            </div>
          </div>
          <div className="scroll-x" style={{ padding: "10px 14px 14px" }}>
            <div style={{ position: "relative", width }} onMouseDown={(e) => {
              if ((e.target as HTMLElement).dataset.bg !== "1") return;
              const r = e.currentTarget.getBoundingClientRect();
              seek((e.clientX - r.left) / pps);
            }}>
              {/* ruler */}
              <div data-bg="1" style={{ height: 20, position: "relative", borderBottom: "1px solid var(--border)", marginBottom: 6, cursor: "pointer" }}>
                {Array.from({ length: Math.ceil(total) + 1 }).map((_, s) => (s % (pps < 16 ? 5 : pps < 30 ? 2 : 1) === 0) && (
                  <span key={s} data-bg="1" className="tiny faint mono" style={{ position: "absolute", left: px(s), top: 2, borderLeft: "1px solid var(--border-2)", paddingLeft: 3 }}>{fmtDuration(s)}</span>
                ))}
              </div>
              <TrackLabel icon={<Film size={12} />} label="Video" />
              <div data-bg="1" style={{ position: "relative", height: TRACK_H + 6 }}>
                {tl.ranges.map((r, i) => {
                  const start = srcToOut(tl.ranges, r.start) ?? 0;
                  return (
                    <RangeBlock key={i} left={px(start)} width={px(r.end - r.start)} pps={pps} selected={sel?.kind === "range" && sel.index === i}
                      label={`${fmtDuration(r.start)}–${fmtDuration(r.end)}`} onSelect={() => setSel({ kind: "range", index: i })}
                      onTrim={(edge, dxSec) => commit(trimRange(tl, i, edge, (edge === "start" ? r.start : r.end) + dxSec, video?.duration_s ?? r.end + 60))} />
                  );
                })}
              </div>
              <TrackLabel icon={<Crosshair size={12} />} label="Layout" />
              <div style={{ position: "relative", height: 20, marginBottom: 6 }}>
                {tl.layouts.map((l, i) => (
                  <div key={i} onClick={() => setSel({ kind: "layout", index: i })} className="tiny"
                    style={{ position: "absolute", left: px(l.start), width: Math.max(2, px(l.end - l.start)), top: 0, height: 18, borderRadius: 5, cursor: "pointer",
                      background: l.layout === "fit" ? "rgba(34,211,238,.25)" : l.layout === "split" ? "rgba(244,114,182,.28)" : "rgba(139,92,246,.22)",
                      outline: sel?.kind === "layout" && sel.index === i ? "1px solid #fff" : "none", padding: "1px 6px", overflow: "hidden", whiteSpace: "nowrap" }}>{l.layout}</div>
                ))}
              </div>
              <TrackLabel icon={<Type size={12} />} label="Captions" />
              <div style={{ position: "relative", height: 26, marginBottom: 6 }}>
                {tl.captions.words.map((w, i) => (
                  <div key={i} title={w.text} onClick={() => { setSel({ kind: "word", index: i }); setPanel("captions"); seek(w.start); }}
                    onDoubleClick={() => { const words = [...tl.captions.words]; words[i] = { ...w, emphasis: !w.emphasis }; upd({ captions: { ...tl.captions, words } }); }}
                    style={{ position: "absolute", left: px(w.start), width: Math.max(3, px(w.end - w.start) - 1), height: 24, borderRadius: 4, fontSize: 10,
                      overflow: "hidden", whiteSpace: "nowrap", padding: "4px 3px", cursor: "pointer",
                      background: w.emphasis ? "rgba(251,191,36,.3)" : "rgba(255,255,255,.08)",
                      outline: sel?.kind === "word" && sel.index === i ? "1px solid #fff" : "none" }}>{px(w.end - w.start) > 18 ? w.text : ""}</div>
                ))}
              </div>
              <TrackLabel icon={<ZoomIn size={12} />} label="Zoom · Text · B-roll" />
              <div style={{ position: "relative", height: 22 }}>
                {tl.zooms.map((z, i) => (
                  <Pill key={`z${i}`} left={px(z.start)} width={px(z.end - z.start)} color="rgba(139,92,246,.45)" selected={sel?.kind === "zoom" && sel.index === i}
                    onClick={() => setSel({ kind: "zoom", index: i })}>{Math.round((z.scale - 1) * 100)}%</Pill>
                ))}
                {tl.overlays.map((o, i) => (
                  <Pill key={`o${i}`} left={px(o.start)} width={px(o.end - o.start)} color="rgba(255,255,255,.3)" selected={sel?.kind === "overlay" && sel.index === i}
                    onClick={() => setSel({ kind: "overlay", index: i })} top={0}>{o.text}</Pill>
                ))}
                {tl.broll.map((b, i) => (
                  <Pill key={`b${i}`} left={px(b.start)} width={px(b.end - b.start)} color="rgba(34,211,238,.45)" selected={sel?.kind === "broll" && sel.index === i}
                    onClick={() => setSel({ kind: "broll", index: i })}>{b.label ?? "B-roll"}</Pill>
                ))}
              </div>
              {cuts.map((c, i) => <div key={i} style={{ position: "absolute", left: px(c), top: 22, bottom: 0, width: 1, background: "rgba(248,113,113,.5)", pointerEvents: "none" }} />)}
              <div style={{ position: "absolute", left: px(t), top: 0, bottom: 0, width: 2, background: "#fff", boxShadow: "0 0 8px rgba(255,255,255,.8)", pointerEvents: "none" }} />
            </div>
          </div>
          <SelectionBar tl={tl} sel={sel} setSel={setSel} commit={commit} total={total} />
        </div>

        <div className="card">
          <div className="tabs" style={{ margin: 0, padding: "0 10px" }}>
            {(["captions", "framing", "audio", "look"] as const).map((p) => <button key={p} className={clsx("tab", panel === p && "on")} onClick={() => setPanel(p)}>{p[0].toUpperCase() + p.slice(1)}</button>)}
          </div>
          <div className="card-pad" style={{ paddingTop: 6 }}>
            {panel === "captions" && (
              <>
                <FieldRow label="Captions"><Toggle on={tl.captions.enabled} onChange={(v) => upd({ captions: { ...tl.captions, enabled: v } })} /></FieldRow>
                <label className="label" style={{ marginTop: 10 }}>Style</label>
                <select className="select" value={tl.captions.preset} onChange={(e) => upd({ captions: { ...tl.captions, preset: e.target.value } })}>
                  {presets?.map((p) => <option key={p.name}>{p.name}</option>)}
                </select>
                <label className="label" style={{ marginTop: 12 }}>Vertical position · {Math.round((tl.captions.vertical_position ?? preset?.position ?? 0.68) * 100)}%</label>
                <input type="range" className="range" min={0.2} max={0.78} step={0.01} value={tl.captions.vertical_position ?? preset?.position ?? 0.68}
                  onChange={(e) => setTl({ ...tl, captions: { ...tl.captions, vertical_position: Number(e.target.value) } })}
                  onMouseUp={() => commit({ ...tl })} />
                <label className="label" style={{ marginTop: 12 }}>Words per caption</label>
                <Segmented value={String(tl.captions.max_words ?? preset?.max_words ?? 3)} onChange={(v) => upd({ captions: { ...tl.captions, max_words: Number(v) } })}
                  options={["1", "2", "3", "4", "5", "6"].map((v) => ({ value: v, label: v }))} />
                {selWord && sel?.kind === "word" && (
                  <div className="card card-pad" style={{ marginTop: 14 }}>
                    <div className="small strong">Selected word · {fmtTimecode(selWord.start)}</div>
                    <input className="input" style={{ marginTop: 8 }} value={selWord.text}
                      onChange={(e) => { const words = [...tl.captions.words]; words[sel.index] = { ...selWord, text: e.target.value }; setTl({ ...tl, captions: { ...tl.captions, words } }); }}
                      onBlur={() => commit({ ...tl })} />
                    <FieldRow label="Emphasis"><Toggle on={selWord.emphasis} onChange={(v) => { const words = [...tl.captions.words]; words[sel.index] = { ...selWord, emphasis: v }; upd({ captions: { ...tl.captions, words } }); }} /></FieldRow>
                    <Button size="sm" variant="danger" onClick={() => { upd({ captions: { ...tl.captions, words: tl.captions.words.filter((_, i) => i !== sel.index) } }); setSel(null); }}><Trash2 size={13} /> Remove word</Button>
                  </div>
                )}
                <div className="tiny faint" style={{ marginTop: 12 }}>Click a word on the Captions track to edit its text; double-click toggles emphasis.</div>
              </>
            )}
            {panel === "framing" && (
              <>
                <div className="small muted" style={{ marginBottom: 8 }}>Layout at playhead</div>
                <Segmented value={(tl.layouts[currentLayoutIdx]?.layout ?? "crop") as "crop" | "fit" | "split"}
                  onChange={(v) => { if (currentLayoutIdx < 0) return; const layouts = [...tl.layouts]; layouts[currentLayoutIdx] = { ...layouts[currentLayoutIdx], layout: v, secondary: v === "split" ? layouts[currentLayoutIdx].secondary : [] }; upd({ layouts }); }}
                  options={[{ value: "crop", label: "Crop" }, { value: "fit", label: "Fit + blur" }, { value: "split", label: "Split" }]} />
                <label className="label" style={{ marginTop: 16 }}>Camera position at playhead · {Math.round(cropAt(tl, t).cx * 100)}%</label>
                <input type="range" className="range" min={0} max={1} step={0.005} value={cropAt(tl, t).cx}
                  onChange={(e) => setTl(pinCrop(tl, t, Number(e.target.value)))} onMouseUp={() => commit({ ...tl })} />
                <div className="tiny faint">Dragging pins the virtual camera for 1.5 s from the playhead (smoothly blended by the renderer).</div>
                <FieldRow label="Punch-ins" desc={`${tl.zooms.length} events`}>
                  <Button size="sm" variant="ghost" onClick={() => upd({ zooms: [] })}>Clear all</Button>
                </FieldRow>
                <Button size="sm" style={{ marginTop: 10 }} onClick={() => api.renderShort(sid, true).then(() => toast({ title: "Re-planning edit automatically", level: "info" }))}>
                  <Wand2 size={13} /> Re-plan reframing automatically</Button>
              </>
            )}
            {panel === "audio" && (
              <>
                <label className="label">Loudness target · {tl.audio.target_lufs} LUFS</label>
                <input type="range" className="range" min={-20} max={-10} step={0.5} value={tl.audio.target_lufs} onChange={(e) => upd({ audio: { ...tl.audio, target_lufs: Number(e.target.value) } })} />
                <label className="label" style={{ marginTop: 10 }}>Gain · {tl.audio.gain_db} dB</label>
                <input type="range" className="range" min={-12} max={12} step={0.5} value={tl.audio.gain_db} onChange={(e) => upd({ audio: { ...tl.audio, gain_db: Number(e.target.value) } })} />
                <FieldRow label="Compressor"><Toggle on={tl.audio.compressor} onChange={(v) => upd({ audio: { ...tl.audio, compressor: v } })} /></FieldRow>
                <FieldRow label="Voice EQ"><Toggle on={tl.audio.eq} onChange={(v) => upd({ audio: { ...tl.audio, eq: v } })} /></FieldRow>
                <FieldRow label="Noise reduction"><Toggle on={tl.audio.denoise} onChange={(v) => upd({ audio: { ...tl.audio, denoise: v } })} /></FieldRow>
                <div className="divider" />
                <div className="row small strong"><Music size={14} /> Music</div>
                {tl.music ? (
                  <>
                    <div className="tiny faint ellipsis" style={{ margin: "6px 0" }}>{tl.music.path}</div>
                    <label className="label">Volume · {tl.music.volume_db} dB</label>
                    <input type="range" className="range" min={-40} max={-6} value={tl.music.volume_db} onChange={(e) => upd({ music: { ...tl.music!, volume_db: Number(e.target.value) } })} />
                    <label className="label">Ducking under speech · {tl.music.duck_db} dB</label>
                    <input type="range" className="range" min={-24} max={0} value={tl.music.duck_db} onChange={(e) => upd({ music: { ...tl.music!, duck_db: Number(e.target.value) } })} />
                    <Button size="sm" variant="danger" onClick={() => upd({ music: null })}><Trash2 size={13} /> Remove music</Button>
                  </>
                ) : (
                  <Button size="sm" style={{ marginTop: 8 }} onClick={() => { const p = prompt("Full path to a music file (mp3/wav/m4a)"); if (p) upd({ music: { path: p, volume_db: -22, duck_db: -10, offset: 0, fade_in: 0.6, fade_out: 1.2 } }); }}>
                    <Plus size={13} /> Add music</Button>
                )}
              </>
            )}
            {panel === "look" && (
              <>
                {(["sharpen", "contrast", "color", "vignette", "denoise"] as const).map((k) => (
                  <FieldRow key={k} label={{ sharpen: "Sharpen", contrast: "Contrast boost", color: "Color pop", vignette: "Subtle vignette", denoise: "Video denoise" }[k]}>
                    <Toggle on={tl.enhance[k]} onChange={(v) => upd({ enhance: { ...tl.enhance, [k]: v } })} />
                  </FieldRow>
                ))}
                <div className="divider" />
                <div className="small strong" style={{ marginBottom: 6 }}>Safe area</div>
                {(["top", "bottom", "left", "right"] as const).map((k) => (
                  <div key={k}>
                    <label className="label">{k} · {Math.round(tl.safe_area[k] * 100)}%</label>
                    <input type="range" className="range" min={0} max={0.35} step={0.01} value={tl.safe_area[k]} onChange={(e) => upd({ safe_area: { ...tl.safe_area, [k]: Number(e.target.value) } })} />
                  </div>
                ))}
              </>
            )}
          </div>
        </div>
      </div>
      <div className="tiny faint" style={{ marginTop: 12 }}><History size={11} /> Saving creates a new timeline version; earlier versions stay available in the Versions menu.</div>
    </div>
  );
}

function TrackLabel({ icon, label }: { icon: React.ReactNode; label: string }) {
  return <div className="row tiny faint" style={{ gap: 5, margin: "4px 0 3px" }}>{icon}{label}</div>;
}

function Pill({ left, width, color, selected, onClick, children, top = 0 }: { left: number; width: number; color: string; selected: boolean; onClick: () => void; children: React.ReactNode; top?: number }) {
  return (
    <div onClick={onClick} className="tiny" style={{ position: "absolute", left, top, width: Math.max(8, width), height: 20, borderRadius: 6, background: color,
      padding: "2px 6px", overflow: "hidden", whiteSpace: "nowrap", cursor: "pointer", outline: selected ? "1px solid #fff" : "none" }}>{children}</div>
  );
}

function RangeBlock({ left, width, pps, label, selected, onSelect, onTrim }: {
  left: number; width: number; pps: number; label: string; selected: boolean; onSelect: () => void;
  onTrim: (edge: "start" | "end", dxSec: number) => void;
}) {
  const [drag, setDrag] = useState<{ edge: "start" | "end"; dx: number } | null>(null);
  const begin = (edge: "start" | "end") => (e: React.PointerEvent) => {
    e.stopPropagation();
    const startX = e.clientX;
    const move = (ev: PointerEvent) => setDrag({ edge, dx: ev.clientX - startX });
    const up = (ev: PointerEvent) => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      setDrag(null);
      const dx = ev.clientX - startX;
      if (Math.abs(dx) > 2) onTrim(edge, dx / pps);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  // Visual feedback while dragging; the edit is committed on release.
  const l = left + (drag?.edge === "start" ? drag.dx : 0);
  const w = width + (drag?.edge === "start" ? -drag.dx : drag?.edge === "end" ? drag.dx : 0);
  return (
    <div onClick={onSelect} style={{ position: "absolute", left: l, width: Math.max(4, w - 2), height: TRACK_H, borderRadius: 8, cursor: "pointer",
      background: "linear-gradient(180deg, rgba(139,92,246,.55), rgba(99,102,241,.4))", outline: selected ? "2px solid #fff" : "1px solid rgba(255,255,255,.12)",
      display: "flex", alignItems: "center", overflow: "hidden", touchAction: "none" }}>
      <div onPointerDown={begin("start")} style={{ width: 8, alignSelf: "stretch", cursor: "ew-resize", background: "rgba(255,255,255,.25)" }} />
      <span className="tiny grow" style={{ padding: "0 6px", whiteSpace: "nowrap", overflow: "hidden" }}>{label}</span>
      <div onPointerDown={begin("end")} style={{ width: 8, alignSelf: "stretch", cursor: "ew-resize", background: "rgba(255,255,255,.25)" }} />
    </div>
  );
}

function SelectionBar({ tl, sel, setSel, commit, total }: { tl: EditTimeline; sel: Sel; setSel: (s: Sel) => void; commit: (t: EditTimeline) => void; total: number }) {
  if (!sel) return null;
  const remove = () => {
    if (sel.kind === "range") commit(deleteRange(tl, sel.index));
    if (sel.kind === "zoom") commit({ ...tl, zooms: tl.zooms.filter((_, i) => i !== sel.index) });
    if (sel.kind === "overlay") commit({ ...tl, overlays: tl.overlays.filter((_, i) => i !== sel.index) });
    if (sel.kind === "broll") commit({ ...tl, broll: tl.broll.filter((_, i) => i !== sel.index) });
    setSel(null);
  };
  return (
    <div className="row" style={{ gap: 8, padding: "10px 14px", borderTop: "1px solid var(--border)", flexWrap: "wrap" }}>
      <Badge color="violet">{sel.kind}</Badge>
      {sel.kind === "zoom" && tl.zooms[sel.index] && (
        <>
          <span className="small muted">Scale</span>
          <input type="range" className="range" min={1.02} max={1.2} step={0.01} value={tl.zooms[sel.index].scale} style={{ width: 120 }}
            onChange={(e) => { const zooms = [...tl.zooms]; zooms[sel.index] = { ...zooms[sel.index], scale: Number(e.target.value) }; commit({ ...tl, zooms }); }} />
          <span className="mono small">{Math.round((tl.zooms[sel.index].scale - 1) * 100)}%</span>
        </>
      )}
      {sel.kind === "overlay" && tl.overlays[sel.index] && (
        <input className="input" style={{ width: 280, height: 28 }} value={tl.overlays[sel.index].text}
          onChange={(e) => { const overlays = [...tl.overlays]; overlays[sel.index] = { ...overlays[sel.index], text: e.target.value }; commit({ ...tl, overlays }); }} />
      )}
      {sel.kind === "broll" && tl.broll[sel.index] && (
        <Segmented value={tl.broll[sel.index].mode as "full" | "pip"} options={[{ value: "full", label: "Full screen" }, { value: "pip", label: "Picture-in-picture" }]}
          onChange={(v) => { const broll = [...tl.broll]; broll[sel.index] = { ...broll[sel.index], mode: v }; commit({ ...tl, broll }); }} />
      )}
      {sel.kind === "range" && <span className="small faint">Drag the handles to trim · removing a segment re-times captions automatically</span>}
      {sel.kind !== "word" && sel.kind !== "layout" && (
        <Button size="sm" variant="danger" className="right" disabled={sel.kind === "range" && tl.ranges.length <= 1} onClick={remove}><Trash2 size={13} /> Delete</Button>
      )}
      <span className="tiny faint">{fmtDuration(total)} total</span>
    </div>
  );
}

