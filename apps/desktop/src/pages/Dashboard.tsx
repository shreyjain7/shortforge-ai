import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { ArrowRight, Clapperboard, Download, Film, HardDrive, Layers, Plus, Radio, Send, Sparkles, UploadCloud } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { PageHeader, Progress, Skeleton, Stat, StatusBadge, stagger, Button, Empty, ScoreRing } from "../components/ui";
import { api, mediaUrl } from "../lib/api";
import { useEvents } from "../lib/events";
import { fmtBytes, fmtDuration, relTime } from "../lib/format";

export default function Dashboard() {
  const nav = useNavigate();
  const { progress } = useEvents();
  const { data, isLoading } = useQuery({ queryKey: ["dashboard"], queryFn: api.dashboard, refetchInterval: 10000 });
  const { data: stats } = useQuery({ queryKey: ["stats"], queryFn: api.stats, refetchInterval: 2500 });
  const { data: hw } = useQuery({ queryKey: ["hardware"], queryFn: () => api.hardware() });
  const c = data?.counts ?? {};
  const live = stats?.live;
  const cards = [
    { label: "Channels monitored", value: c.channels_monitored, icon: <Radio size={13} />, sub: `${c.sources ?? 0} sources total` },
    { label: "Videos discovered", value: c.videos_discovered, icon: <Film size={13} />, sub: `${c.videos_downloaded ?? 0} downloaded` },
    { label: "Videos processed", value: c.videos_processed, icon: <Layers size={13} /> },
    { label: "Candidate clips", value: c.candidate_clips, icon: <Sparkles size={13} /> },
    { label: "Shorts generated", value: c.shorts_generated, icon: <Clapperboard size={13} />, accent: true },
    { label: "Ready for review", value: c.shorts_ready, icon: <Download size={13} /> },
    { label: "Scheduled", value: c.scheduled, icon: <UploadCloud size={13} /> },
    { label: "Uploaded today", value: c.uploaded_today, icon: <Send size={13} /> },
  ];
  const storage = data?.storage;
  return (
    <div className="page">
      <PageHeader
        title="Studio overview"
        subtitle={hw ? `${hw.gpus?.[0]?.name ?? "CPU only"} · ${hw.cpu_name} · ${hw.ram_total_gb} GB RAM` : "Local AI Shorts studio"}
        actions={<>
          <Button onClick={() => nav("/shorts")}>Open library</Button>
          <Button variant="primary" onClick={() => nav("/sources?add=1")}><Plus size={15} /> Add source</Button>
        </>}
      />
      <div className="grid grid-4" style={{ marginBottom: 14 }}>
        {cards.map((s, i) => (
          <motion.div key={s.label} {...stagger(i)}>
            {isLoading ? <div className="card stat"><Skeleton h={12} w={90} /><div style={{ height: 10 }} /><Skeleton h={26} w={60} /></div>
              : <Stat label={s.label} value={s.value ?? 0} icon={s.icon} sub={s.sub} accent={s.accent} />}
          </motion.div>
        ))}
      </div>

      <div className="grid" style={{ gridTemplateColumns: "minmax(0, 2fr) minmax(300px, 1fr)", marginBottom: 14 }}>
        <div className="card">
          <div className="card-header">
            <span className="card-title">Active tasks</span>
            <span className="card-sub">{(stats?.queue.counts.queued ?? 0)} queued</span>
            <Button size="sm" variant="ghost" className="right" onClick={() => nav("/queue")}>Queue <ArrowRight size={13} /></Button>
          </div>
          <div className="card-pad col" style={{ gap: 12, minHeight: 120 }}>
            {(stats?.queue.running ?? []).length === 0 && (
              <div className="faint small" style={{ padding: "18px 0", textAlign: "center" }}>Idle — nothing is processing right now.</div>
            )}
            {(stats?.queue.running ?? []).map((j) => {
              const p = progress[j.id]?.progress ?? j.progress;
              const msg = progress[j.id]?.message ?? j.message;
              return (
                <div key={j.id} className="col" style={{ gap: 6 }}>
                  <div className="row small">
                    <span className="strong">{j.label}</span>
                    <span className="faint">#{j.id}{j.video_id ? ` · video ${j.video_id}` : ""}{j.short_id ? ` · short ${j.short_id}` : ""}</span>
                    <span className="right mono faint">{Math.round(p * 100)}%</span>
                  </div>
                  <Progress value={p} />
                  <div className="tiny faint ellipsis">{msg}</div>
                </div>
              );
            })}
          </div>
        </div>
        <div className="card">
          <div className="card-header"><span className="card-title">Hardware</span>
            {stats?.models_loaded?.length ? <span className="badge violet right">{stats.models_loaded.map((m) => m.name.split(":")[1] ?? m.name).join(", ")}</span> : null}
          </div>
          <div className="card-pad col" style={{ gap: 12 }}>
            {live && [
              { label: "GPU utilization", v: (live.gpu_util ?? 0) / 100, t: live.gpu_util !== null ? `${Math.round(live.gpu_util ?? 0)}%` : "n/a" },
              { label: "VRAM", v: live.vram_total_mb ? (live.vram_used_mb ?? 0) / live.vram_total_mb : 0,
                t: live.vram_total_mb ? `${((live.vram_used_mb ?? 0) / 1024).toFixed(1)} / ${(live.vram_total_mb / 1024).toFixed(1)} GB` : "n/a" },
              { label: "CPU", v: live.cpu_percent / 100, t: `${Math.round(live.cpu_percent)}%` },
              { label: "RAM", v: live.ram_percent / 100, t: `${live.ram_used_gb.toFixed(1)} / ${live.ram_total_gb.toFixed(0)} GB` },
            ].map((m) => (
              <div key={m.label}>
                <div className="row small"><span className="muted">{m.label}</span><span className="right strong mono">{m.t}</span></div>
                <div style={{ marginTop: 5 }}><Progress value={m.v} thin color={m.v > 0.9 ? "red" : m.v > 0.75 ? "yellow" : undefined} /></div>
              </div>
            ))}
            {live?.gpu_temp_c != null && <div className="tiny faint">GPU {Math.round(live.gpu_temp_c)}°C · {live.gpu_power_w ?? "—"} W · NVENC {live.encoder_util ?? 0}%</div>}
          </div>
        </div>
      </div>

      <div className="grid" style={{ gridTemplateColumns: "minmax(0, 2fr) minmax(300px, 1fr)" }}>
        <div className="card">
          <div className="card-header">
            <span className="card-title">Recent Shorts</span>
            <Button size="sm" variant="ghost" className="right" onClick={() => nav("/shorts")}>All Shorts <ArrowRight size={13} /></Button>
          </div>
          <div className="card-pad">
            {data && data.recent_shorts.length === 0 ? (
              <Empty icon={<Clapperboard size={22} />} title="No Shorts yet"
                action={<Button variant="primary" onClick={() => nav("/sources?add=1")}><Plus size={14} /> Add a YouTube channel or video</Button>}>
                Add any public YouTube channel, @handle, playlist or video — ShortForge finds the strongest moments and renders them.
              </Empty>
            ) : (
              <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(132px, 1fr))" }}>
                {(data?.recent_shorts ?? []).map((s, i) => (
                  <motion.div key={s.id} {...stagger(i)} whileHover={{ y: -3 }} style={{ cursor: "pointer" }} onClick={() => nav(`/shorts/${s.id}`)}>
                    <div className="vthumb">
                      {s.cover_url ? <img src={mediaUrl(s.cover_url)} /> : <div className="center skeleton" style={{ height: "100%" }} />}
                      <div style={{ position: "absolute", top: 7, left: 7 }}><StatusBadge status={s.status} /></div>
                      <div style={{ position: "absolute", top: 5, right: 5 }}><ScoreRing score={s.score} size={30} stroke={3} /></div>
                    </div>
                    <div className="small strong clamp-2" style={{ marginTop: 7 }}>{s.title}</div>
                    <div className="tiny faint">{fmtDuration(s.duration)} · {relTime(s.created_at)}</div>
                  </motion.div>
                ))}
              </div>
            )}
          </div>
        </div>
        <div className="card">
          <div className="card-header"><HardDrive size={15} /><span className="card-title">Storage</span>
            <span className="card-sub right">{storage ? fmtBytes(storage.disk_free) + " free" : ""}</span></div>
          <div className="card-pad col" style={{ gap: 10 }}>
            {storage && Object.entries(storage.folders).sort((a, b) => b[1] - a[1]).map(([k, v]) => (
              <div key={k}>
                <div className="row small"><span className="muted">{k}</span><span className="right mono">{fmtBytes(v)}</span></div>
                <div style={{ marginTop: 4 }}><Progress thin value={storage.total_bytes ? v / storage.total_bytes : 0} /></div>
              </div>
            ))}
            {storage && <div className="tiny faint ellipsis" title={storage.root}>{storage.root}</div>}
          </div>
        </div>
      </div>
    </div>
  );
}
