import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import {
  Activity,
  BarChart3,
  Bell,
  Boxes,
  CalendarClock,
  CheckCircle2,
  Clapperboard,
  Cpu,
  Film,
  LayoutDashboard,
  ListVideo,
  Pause,
  Radio,
  Scissors,
  Send,
  Settings as SettingsIcon,
  Sparkles,
  Type,
  AlertTriangle,
  XCircle,
  Info,
} from "lucide-react";
import type { ReactNode } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { useEvents } from "../lib/events";
import { useUpdater } from "./UpdateBanner";

const NAV: { to: string; label: string; icon: ReactNode; section?: string; countKey?: string }[] = [
  { to: "/", label: "Dashboard", icon: <LayoutDashboard size={16} /> },
  { to: "/sources", label: "Sources", icon: <Radio size={16} />, section: "Pipeline" },
  { to: "/videos", label: "Videos", icon: <Film size={16} /> },
  { to: "/candidates", label: "Candidates", icon: <Sparkles size={16} /> },
  { to: "/shorts", label: "Shorts", icon: <Clapperboard size={16} />, countKey: "shorts_ready" },
  { to: "/editor", label: "Editor", icon: <Scissors size={16} /> },
  { to: "/queue", label: "Queue", icon: <ListVideo size={16} />, countKey: "jobs_queued" },
  { to: "/schedule", label: "Schedule", icon: <CalendarClock size={16} />, section: "Publishing", countKey: "scheduled" },
  { to: "/published", label: "Published", icon: <Send size={16} /> },
  { to: "/analytics", label: "Analytics", icon: <BarChart3 size={16} /> },
  { to: "/templates", label: "Templates", icon: <Type size={16} />, section: "Studio" },
  { to: "/models", label: "Models", icon: <Boxes size={16} /> },
  { to: "/settings", label: "Settings", icon: <SettingsIcon size={16} /> },
];

const TITLES: Record<string, string> = Object.fromEntries(NAV.map((n) => [n.to, n.label]));

function Meter({ label, value, text }: { label: string; value: number | null; text: string }) {
  const v = value ?? 0;
  const color = v > 0.9 ? "red" : v > 0.75 ? "yellow" : "";
  return (
    <div className="meter" title={label}>
      <span>{label}</span>
      <div className={clsx("bar thin", color)}><span style={{ width: `${Math.min(100, v * 100)}%` }} /></div>
      <b>{text}</b>
    </div>
  );
}

export function Topbar() {
  const loc = useLocation();
  const nav = useNavigate();
  const { connected } = useEvents();
  const { data: stats } = useQuery({ queryKey: ["stats"], queryFn: api.stats, refetchInterval: 2500 });
  const { data: dash } = useQuery({ queryKey: ["dashboard"], queryFn: api.dashboard, refetchInterval: 15000 });
  const live = stats?.live;
  const base = "/" + (loc.pathname.split("/")[1] ?? "");
  const running = stats?.queue.running ?? [];
  const title = TITLES[base] ?? "ShortForge";
  return (
    <header className="topbar">
      <div className="topbar-title">{title}</div>
      <AnimatePresence>
        {running.length > 0 && (
          <motion.button className="meter" style={{ cursor: "pointer", maxWidth: 420 }} onClick={() => nav("/queue")}
            initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
            <Activity size={13} className="pulse" style={{ color: "var(--accent-2)" }} />
            <span className="ellipsis" style={{ maxWidth: 300 }}>
              {running[0].label}: {running[0].message ?? ""}
            </span>
            {running.length > 1 && <b>+{running.length - 1}</b>}
          </motion.button>
        )}
      </AnimatePresence>
      <div className="topbar-spacer" />
      {stats?.queue.paused && <span className="badge yellow"><Pause size={11} /> Queue paused</span>}
      {live && (
        <div className="row" style={{ gap: 6 }}>
          {live.gpu_util !== null && <Meter label="GPU" value={(live.gpu_util ?? 0) / 100} text={`${Math.round(live.gpu_util ?? 0)}%`} />}
          {live.vram_total_mb && (
            <Meter label="VRAM" value={(live.vram_used_mb ?? 0) / live.vram_total_mb}
              text={`${((live.vram_used_mb ?? 0) / 1024).toFixed(1)}G`} />
          )}
          <Meter label="CPU" value={live.cpu_percent / 100} text={`${Math.round(live.cpu_percent)}%`} />
          <Meter label="RAM" value={live.ram_percent / 100} text={`${live.ram_used_gb.toFixed(1)}G`} />
          {live.gpu_temp_c !== null && <div className="meter"><Cpu size={12} /><b>{Math.round(live.gpu_temp_c)}°</b></div>}
        </div>
      )}
      <button className="btn ghost icon sm" onClick={() => nav("/settings?tab=notifications")} title="Notifications"
        style={{ position: "relative" }}>
        <Bell size={15} />
        {(dash?.unread_notifications ?? 0) > 0 && (
          <span style={{ position: "absolute", top: 3, right: 4, width: 7, height: 7, borderRadius: 7, background: "var(--accent)" }} />
        )}
      </button>
      <span title={connected ? "Engine connected" : "Engine offline"}
        style={{ width: 8, height: 8, borderRadius: 8, background: connected ? "var(--success)" : "var(--danger)",
          boxShadow: connected ? "0 0 10px rgba(52,211,153,.6)" : "none" }} />
    </header>
  );
}

export function Sidebar() {
  const { data: dash } = useQuery({ queryKey: ["dashboard"], queryFn: api.dashboard, refetchInterval: 15000 });
  const { data: settings } = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const autopilot = settings?.autopilot?.enabled;
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-logo">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><rect x="6" y="2" width="12" height="20" rx="3.5" stroke="#fff" strokeWidth="2.2" /><path d="M10.5 8.5l4.5 3.5-4.5 3.5z" fill="#fff" /></svg>
        </div>
        <div className="brand-text">
          <div className="brand-name">ShortForge</div>
          <div className="brand-sub">AI Studio</div>
        </div>
      </div>
      {NAV.map((n) => {
        const count = n.countKey ? dash?.counts?.[n.countKey] : undefined;
        return (
          <div key={n.to}>
            {n.section && <div className="nav-section">{n.section}</div>}
            <NavLink to={n.to} end={n.to === "/"} className={({ isActive }) => clsx("nav-item", isActive && "active")}>
              {n.icon}
              <span className="label-text">{n.label}</span>
              {count ? <span className={clsx("count", n.countKey === "shorts_ready" && "hot")}>{count}</span> : null}
            </NavLink>
          </div>
        );
      })}
      <div className="sidebar-footer">
        <SidebarVersion />
        <NavLink to="/settings?tab=autopilot" className="card" style={{ display: "block", padding: "10px 12px", borderRadius: 12 }}>
          <div className="row">
            <span style={{ width: 8, height: 8, borderRadius: 8, background: autopilot ? "var(--success)" : "var(--text-3)",
              boxShadow: autopilot ? "0 0 10px rgba(52,211,153,.7)" : "none" }} />
            <span className="strong">Autopilot</span>
            <span className="right tiny faint hide-sm">{autopilot ? "ON" : "OFF"}</span>
          </div>
          <div className="tiny faint hide-sm" style={{ marginTop: 4 }}>
            {autopilot ? "Monitoring sources automatically" : "Manual mode"}
          </div>
        </NavLink>
      </div>
    </aside>
  );
}

function SidebarVersion() {
  const nav = useNavigate();
  const u = useUpdater();
  const { data: health } = useQuery({ queryKey: ["health"], queryFn: api.health });
  const hasUpdate = u.phase === "available" || u.phase === "downloading";
  return (
    <button className="row tiny faint hide-sm" onClick={() => nav("/settings?tab=about")}
      style={{ background: "none", border: "none", cursor: "pointer", padding: "0 6px 10px", gap: 6, color: "var(--text-3)" }}>
      <span>v{health?.version ?? "…"}</span>
      {hasUpdate && <span className="badge violet" style={{ height: 17 }}>Update {u.update?.version}</span>}
    </button>
  );
}

export function Toasts() {
  const { toasts, dismiss } = useEvents();
  const nav = useNavigate();
  const icon = { success: <CheckCircle2 size={17} color="var(--success)" />, error: <XCircle size={17} color="var(--danger)" />,
    warning: <AlertTriangle size={17} color="var(--warning)" />, info: <Info size={17} color="var(--info)" /> };
  return (
    <div className="toasts">
      <AnimatePresence>
        {toasts.map((t) => (
          <motion.div key={t.id} className="toast glass" layout initial={{ opacity: 0, x: 30, scale: 0.97 }}
            animate={{ opacity: 1, x: 0, scale: 1 }} exit={{ opacity: 0, x: 30 }}
            onClick={() => { if (t.link?.startsWith("/")) nav(t.link); dismiss(t.id); }} style={{ cursor: "pointer" }}>
            {icon[t.level]}
            <div className="grow">
              <div className="t-title">{t.title}</div>
              {t.body && <div className="t-body clamp-2">{t.body}</div>}
            </div>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}
