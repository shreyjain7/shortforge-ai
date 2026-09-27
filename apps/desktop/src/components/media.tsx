import clsx from "clsx";
import { motion } from "framer-motion";
import { Pause, Play, ShieldCheck, Volume2, VolumeX } from "lucide-react";
import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { mediaUrl } from "../lib/api";
import { fmtDuration, LABEL_COLORS, scoreColor, titleCase } from "../lib/format";
import type { Candidate, ScoreRow, TimelineSeg } from "../lib/types";

export interface PlayerHandle {
  seek: (t: number) => void;
  play: () => void;
  pause: () => void;
  video: HTMLVideoElement | null;
}

interface PhoneProps {
  src?: string | null;
  poster?: string | null;
  safeArea?: { top: number; bottom: number; left: number; right: number };
  showSafe?: boolean;
  onTime?: (t: number) => void;
  width?: number | string;
  autoPlay?: boolean;
  overlay?: React.ReactNode;
}

export const PhonePlayer = forwardRef<PlayerHandle, PhoneProps>(function PhonePlayer(
  { src, poster, safeArea = { top: 0.08, bottom: 0.22, left: 0.06, right: 0.14 }, showSafe, onTime, width = 300, autoPlay, overlay },
  ref,
) {
  const v = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(false);
  const [t, setT] = useState(0);
  const [dur, setDur] = useState(0);
  useImperativeHandle(ref, () => ({
    seek: (time) => { if (v.current) v.current.currentTime = time; },
    play: () => void v.current?.play(),
    pause: () => v.current?.pause(),
    video: v.current,
  }));
  useEffect(() => {
    setPlaying(false);
    setT(0);
  }, [src]);
  const toggle = () => {
    if (!v.current) return;
    if (v.current.paused) void v.current.play();
    else v.current.pause();
  };
  return (
    <div style={{ width }}>
      <div className="phone" onClick={toggle} style={{ cursor: "pointer" }}>
        {src ? (
          <video
            ref={v}
            src={mediaUrl(src)}
            poster={mediaUrl(poster ?? undefined)}
            playsInline
            autoPlay={autoPlay}
            muted={muted}
            preload="metadata"
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onLoadedMetadata={(e) => setDur(e.currentTarget.duration)}
            onTimeUpdate={(e) => {
              setT(e.currentTarget.currentTime);
              onTime?.(e.currentTarget.currentTime);
            }}
          />
        ) : poster ? (
          <img src={mediaUrl(poster)} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
        ) : (
          <div className="center" style={{ height: "100%" }}><span className="faint">No render yet</span></div>
        )}
        {overlay}
        {showSafe && (
          <div className="safe-overlay">
            <div className="zone" style={{ left: 0, right: 0, top: 0, height: `${safeArea.top * 100}%` }}><span className="label">top UI</span></div>
            <div className="zone" style={{ left: 0, right: 0, bottom: 0, height: `${safeArea.bottom * 100}%` }}><span className="label">title / channel</span></div>
            <div className="zone" style={{ right: 0, top: `${safeArea.top * 100}%`, bottom: `${safeArea.bottom * 100}%`, width: `${safeArea.right * 100}%` }}>
              <span className="label">actions</span>
            </div>
          </div>
        )}
        {src && !playing && (
          <div className="center" style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
            <motion.div initial={{ scale: 0.8, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}
              style={{ width: 54, height: 54, borderRadius: 54, background: "rgba(0,0,0,.55)", display: "grid", placeItems: "center",
                backdropFilter: "blur(8px)", border: "1px solid rgba(255,255,255,.2)" }}>
              <Play size={22} fill="#fff" />
            </motion.div>
          </div>
        )}
      </div>
      {src && (
        <div className="row" style={{ marginTop: 10, gap: 6 }}>
          <button className="btn sm icon ghost" onClick={toggle}>{playing ? <Pause size={14} /> : <Play size={14} />}</button>
          <input className="range grow" type="range" min={0} max={dur || 1} step={0.01} value={t}
            onChange={(e) => { if (v.current) v.current.currentTime = Number(e.target.value); }} />
          <span className="tiny faint mono" style={{ minWidth: 76, textAlign: "right" }}>{fmtDuration(t)} / {fmtDuration(dur)}</span>
          <button className="btn sm icon ghost" onClick={() => setMuted(!muted)}>{muted ? <VolumeX size={14} /> : <Volume2 size={14} />}</button>
        </div>
      )}
    </div>
  );
});

export function SafeToggle({ on, onChange }: { on: boolean; onChange: (v: boolean) => void }) {
  return (
    <button className={clsx("chip", on && "on")} onClick={() => onChange(!on)}>
      <ShieldCheck size={13} /> Safe zones
    </button>
  );
}

export function SemanticTimeline({ duration, segments, cuts = [], candidates = [], playhead, onSeek, onCandidate }: {
  duration: number;
  segments: TimelineSeg[];
  cuts?: number[];
  candidates?: Candidate[];
  playhead?: number;
  onSeek?: (t: number) => void;
  onCandidate?: (c: Candidate) => void;
}) {
  const pct = (t: number) => `${(t / Math.max(1, duration)) * 100}%`;
  return (
    <div>
      <div className="timeline-strip" onClick={(e) => {
        if (!onSeek) return;
        const r = e.currentTarget.getBoundingClientRect();
        onSeek(((e.clientX - r.left) / r.width) * duration);
      }}>
        {segments.map((s, i) => (
          <div key={i} className="seg" title={`${titleCase(s.label)} · ${s.summary ?? ""} (interest ${Math.round(s.interest ?? 0)})`}
            style={{ left: pct(s.start), width: pct(s.end - s.start),
              background: `linear-gradient(180deg, ${LABEL_COLORS[s.label] ?? "#334155"}, ${LABEL_COLORS[s.label] ?? "#334155"}cc)`,
              opacity: 0.35 + 0.65 * ((s.interest ?? 50) / 100) }}>
            {s.end - s.start > duration * 0.06 ? s.label.replace("_", " ") : ""}
          </div>
        ))}
        {cuts.map((c, i) => <div key={`c${i}`} className="marker" style={{ left: pct(c) }} />)}
        {candidates.map((c) => (
          <div key={c.id} className="cand" title={`${c.title ?? "Candidate"} · score ${c.score.toFixed(1)}`}
            style={{ left: pct(c.start), width: pct(c.end - c.start), background: scoreColor(c.score) }}
            onClick={(e) => { e.stopPropagation(); onCandidate?.(c); }} />
        ))}
        {playhead !== undefined && <div className="playhead" style={{ left: pct(playhead) }} />}
      </div>
      <div className="row tiny faint" style={{ justifyContent: "space-between", marginTop: 4 }}>
        <span>0:00</span><span>{fmtDuration(duration / 2)}</span><span>{fmtDuration(duration)}</span>
      </div>
    </div>
  );
}

export function ScoreBreakdown({ rows }: { rows: ScoreRow[] }) {
  const scores = rows.filter((r) => r.kind === "score").sort((a, b) => b.value - a.value);
  const penalties = rows.filter((r) => r.kind === "penalty");
  return (
    <div className="col" style={{ gap: 6 }}>
      {scores.map((r) => (
        <div key={r.metric} className="row" style={{ gap: 10 }}>
          <span className="small muted" style={{ width: 150 }}>{r.label}</span>
          <div className="bar grow"><span style={{ width: `${r.value}%`, background: scoreColor(r.value) }} /></div>
          <span className="small strong mono" style={{ width: 30, textAlign: "right" }}>{Math.round(r.value)}</span>
          <span className={clsx("tiny", r.source === "llm" ? "badge violet" : "faint")} style={{ width: 62, justifyContent: "center" }}>
            {r.source === "llm" ? "LLM+" : r.source}
          </span>
        </div>
      ))}
      {penalties.length > 0 && <div className="divider" style={{ margin: "6px 0" }} />}
      {penalties.map((r) => (
        <div key={r.metric} className="row" style={{ gap: 10 }}>
          <span className="small" style={{ width: 150, color: "var(--danger)" }}>{r.label}</span>
          <span className="grow" />
          <span className="small strong mono" style={{ color: "var(--danger)" }}>{r.value.toFixed(1)}</span>
          <span style={{ width: 62 }} />
        </div>
      ))}
    </div>
  );
}

export function Sparkline({ values, height = 40, color = "var(--accent-2)" }: { values: number[]; height?: number; color?: string }) {
  if (values.length < 2) return <div style={{ height }} />;
  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const w = 200;
  const pts = values.map((v, i) => [(i / (values.length - 1)) * w, height - ((v - min) / (max - min || 1)) * (height - 4) - 2]);
  const d = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${height}`} preserveAspectRatio="none" style={{ width: "100%", height }}>
      <defs>
        <linearGradient id="spark" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stopColor={color} stopOpacity="0.35" />
          <stop offset="1" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={`${d} L${w},${height} L0,${height} Z`} fill="url(#spark)" />
      <path d={d} fill="none" stroke={color} strokeWidth="1.6" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

export function BarList({ items, format = (v: number) => String(v) }: { items: { label: string; value: number; sub?: string }[]; format?: (v: number) => string }) {
  const max = Math.max(...items.map((i) => Math.abs(i.value)), 1);
  return (
    <div className="col" style={{ gap: 8 }}>
      {items.map((it) => (
        <div key={it.label}>
          <div className="row small"><span className="muted ellipsis grow">{it.label}</span><span className="strong mono">{format(it.value)}</span></div>
          <div className="bar thin" style={{ marginTop: 4 }}>
            <span style={{ width: `${(Math.abs(it.value) / max) * 100}%`, background: it.value < 0 ? "var(--danger)" : undefined }} />
          </div>
          {it.sub && <div className="tiny faint" style={{ marginTop: 3 }}>{it.sub}</div>}
        </div>
      ))}
    </div>
  );
}
