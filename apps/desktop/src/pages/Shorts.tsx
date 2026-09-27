import { motion } from "framer-motion";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, Check, Clapperboard, Play, Trash2, Upload } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Empty, PageHeader, Progress, ScoreRing, Segmented, Skeleton, StatusBadge, stagger } from "../components/ui";
import { api, mediaUrl } from "../lib/api";
import { useEvents, useToast } from "../lib/events";
import { fmtDuration, relTime } from "../lib/format";
import type { Short } from "../lib/types";

const STATUSES = ["", "draft", "rendering", "review", "ready", "scheduled", "uploading", "published", "failed"];

export function ShortCard({ s, i, selected, onSelect }: { s: Short; i: number; selected?: boolean; onSelect?: (on: boolean) => void }) {
  const nav = useNavigate();
  const { progress } = useEvents();
  const [hover, setHover] = useState(false);
  const live = Object.values(progress).find((p) => p.short_id === s.id);
  return (
    <motion.div {...stagger(i)} whileHover={{ y: -4 }} onHoverStart={() => setHover(true)} onHoverEnd={() => setHover(false)}
      style={{ cursor: "pointer" }} onClick={() => nav(`/shorts/${s.id}`)}>
      <div className="vthumb" style={{ boxShadow: hover ? "0 20px 50px -20px rgba(139,92,246,.55)" : undefined, transition: "box-shadow .25s" }}>
        {hover && s.video_url ? (
          <video src={mediaUrl(s.video_url)} autoPlay muted loop playsInline />
        ) : s.cover_url ? (
          <img src={mediaUrl(s.cover_url)} loading="lazy" />
        ) : (
          <div className="center" style={{ height: "100%", background: "linear-gradient(160deg,#141527,#0b0c12)" }}>
            <Clapperboard size={26} color="var(--text-3)" />
          </div>
        )}
        <div style={{ position: "absolute", inset: 0, background: "linear-gradient(180deg, rgba(0,0,0,.45) 0%, transparent 25%, transparent 60%, rgba(0,0,0,.75) 100%)", pointerEvents: "none" }} />
        <div style={{ position: "absolute", top: 8, left: 8 }}><StatusBadge status={s.status} /></div>
        {onSelect && (
          <button onClick={(e) => { e.stopPropagation(); onSelect(!selected); }} title="Select"
            style={{ position: "absolute", bottom: 34, right: 8, width: 24, height: 24, borderRadius: 7, cursor: "pointer",
              border: selected ? "none" : "2px solid rgba(255,255,255,.7)", background: selected ? "var(--accent)" : "rgba(0,0,0,.35)",
              display: "grid", placeItems: "center", opacity: selected || hover ? 1 : 0, transition: "opacity .15s" }}>
            {selected && <Check size={14} color="#fff" />}
          </button>
        )}
        <div style={{ position: "absolute", top: 6, right: 6 }}><ScoreRing score={s.score} size={34} stroke={3} /></div>
        {s.qc_status && s.qc_status !== "PASS" && <div style={{ position: "absolute", top: 44, right: 8 }}><StatusBadge status={s.qc_status} /></div>}
        <div style={{ position: "absolute", left: 10, right: 10, bottom: 10 }}>
          {live ? (<><Progress value={live.progress} thin /><div className="tiny" style={{ marginTop: 4, color: "#ddd" }}>{live.message}</div></>) : (
            <div className="row tiny" style={{ color: "#e5e7eb" }}><Play size={11} fill="#fff" /> {fmtDuration(s.duration)}</div>
          )}
        </div>
      </div>
      <div className="small strong clamp-2" style={{ marginTop: 8 }}>{s.title}</div>
      <div className="tiny faint ellipsis">{s.video?.channel_name ?? s.video?.title} · {relTime(s.created_at)}</div>
    </motion.div>
  );
}

export default function Shorts() {
  const [status, setStatus] = useState("");
  const [sort, setSort] = useState<"newest" | "score" | "oldest" | "duration" | "performance">("newest");
  const [source, setSource] = useState<string>("");
  const [minScore, setMinScore] = useState(0);
  const [maxDur, setMaxDur] = useState(180);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const qc = useQueryClient();
  const toast = useToast();
  const bulk = useMutation({
    mutationFn: async (action: "private" | "schedule" | "delete") => {
      const ids = [...selected];
      const results = await Promise.allSettled(ids.map((id) =>
        action === "delete" ? api.deleteShort(id, false)
          : action === "private" ? api.scheduleShort(id, new Date().toISOString(), "private") : api.scheduleShort(id)));
      const failed = results.filter((r) => r.status === "rejected") as PromiseRejectedResult[];
      return { ok: ids.length - failed.length, failed: failed.map((f) => String(f.reason?.message ?? f.reason)) };
    },
    onSuccess: (r, action) => {
      const verb = action === "delete" ? "deleted" : action === "private" ? "queued for private upload" : "scheduled";
      toast({ title: `${r.ok} Short${r.ok === 1 ? "" : "s"} ${verb}`, body: r.failed[0], level: r.failed.length ? "warning" : "success" });
      setSelected(new Set());
      void qc.invalidateQueries({ queryKey: ["shorts"] });
      void qc.invalidateQueries({ queryKey: ["uploads"] });
    },
  });
  const { data: sources } = useQuery({ queryKey: ["sources"], queryFn: api.sources });
  const { data, isLoading } = useQuery({
    queryKey: ["shorts", status, sort, source, minScore, maxDur],
    queryFn: () => api.shorts({ status, sort, source_id: source, min_score: minScore || undefined, max_duration: maxDur < 180 ? maxDur : undefined, limit: 200 }),
    refetchInterval: 10000,
  });
  return (
    <div className="page">
      <PageHeader title="Shorts" subtitle={`${data?.total ?? 0} Shorts`} actions={selected.size > 0 ? (
        <>
          <span className="small muted" style={{ alignSelf: "center" }}>{selected.size} selected</span>
          <Button onClick={() => setSelected(new Set())} variant="ghost">Clear</Button>
          <Button onClick={() => bulk.mutate("private")} loading={bulk.isPending}><Upload size={14} /> Upload privately</Button>
          <Button onClick={() => bulk.mutate("schedule")} loading={bulk.isPending}><CalendarClock size={14} /> Schedule</Button>
          <Button variant="danger" onClick={() => confirm(`Delete ${selected.size} Shorts? Rendered files stay on disk.`) && bulk.mutate("delete")}><Trash2 size={14} /></Button>
        </>
      ) : (data?.items.length ? <Button variant="ghost" onClick={() => setSelected(new Set(data.items.filter((s) => ["ready", "review"].includes(s.status)).map((s) => s.id)))}>Select all ready</Button> : undefined)} />
      <div className="toolbar">
        {STATUSES.map((st) => (
          <button key={st} className={`chip ${status === st ? "on" : ""}`} onClick={() => setStatus(st)}>
            {st ? st[0].toUpperCase() + st.slice(1) : "All"}{st && data?.counts?.[st] ? ` · ${data.counts[st]}` : ""}
          </button>
        ))}
      </div>
      <div className="toolbar">
        <Segmented value={sort} onChange={setSort} options={[{ value: "newest", label: "Newest" }, { value: "score", label: "AI score" },
          { value: "oldest", label: "Oldest" }, { value: "duration", label: "Duration" }, { value: "performance", label: "Performance" }]} />
        <select className="select" style={{ width: 200 }} value={source} onChange={(e) => setSource(e.target.value)}>
          <option value="">All sources</option>
          {sources?.map((s) => <option key={s.id} value={s.id}>{s.title}</option>)}
        </select>
        <div className="row small" style={{ gap: 6 }}>
          <span className="faint">Score ≥</span>
          <input type="range" className="range" min={0} max={90} step={5} value={minScore} style={{ width: 100 }} onChange={(e) => setMinScore(Number(e.target.value))} />
          <span className="mono" style={{ width: 20 }}>{minScore}</span>
        </div>
        <div className="row small" style={{ gap: 6 }}>
          <span className="faint">Length ≤</span>
          <input type="range" className="range" min={15} max={180} step={5} value={maxDur} style={{ width: 100 }} onChange={(e) => setMaxDur(Number(e.target.value))} />
          <span className="mono" style={{ width: 34 }}>{maxDur < 180 ? `${maxDur}s` : "any"}</span>
        </div>
      </div>
      {isLoading && <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(170px, 1fr))" }}>{[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} h={300} r={14} />)}</div>}
      {data?.items.length === 0 && <div className="card"><Empty icon={<Clapperboard size={22} />} title="No Shorts match">Generated Shorts appear here as soon as they render.</Empty></div>}
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(170px, 1fr))", gap: 18 }}>
        {data?.items.map((s, i) => (
          <ShortCard key={s.id} s={s} i={i} selected={selected.has(s.id)}
            onSelect={(on) => setSelected((prev) => { const next = new Set(prev); if (on) next.add(s.id); else next.delete(s.id); return next; })} />
        ))}
      </div>
    </div>
  );
}
