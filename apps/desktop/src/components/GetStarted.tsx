import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { CheckCircle2, ChevronRight, Circle, X } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { API, api } from "../lib/api";
import { Button, Progress } from "./ui";

const KEY = "sf.getstarted.dismissed";

/** Checklist for new users; hides itself once everything is set up (or when dismissed). */
export function GetStarted() {
  const nav = useNavigate();
  const [dismissed, setDismissed] = useState(() => {
    try { return localStorage.getItem(KEY) === "1"; } catch { return false; }
  });
  const { data: tools } = useQuery({ queryKey: ["tools"], queryFn: async () => (await fetch(`${API}/system/tools`)).json() as Promise<{ available: boolean }[]> });
  const { data: models } = useQuery({ queryKey: ["models"], queryFn: api.models });
  const { data: sources } = useQuery({ queryKey: ["sources"], queryFn: api.sources });
  const { data: acct } = useQuery({ queryKey: ["youtube-account"], queryFn: api.youtubeAccount });
  if (!tools || !models || !sources || !acct) return null;
  const steps = [
    { done: tools.every((t) => t.available), title: "Install FFmpeg & a JavaScript runtime", sub: "One click each, no admin rights", to: "/settings?tab=debug" },
    { done: models.items.some((m) => m.kind === "whisper" && m.installed) && models.items.some((m) => m.kind === "llm" && m.installed),
      title: "Install the recommended AI models", sub: "Transcription + a local language model", to: "/models" },
    { done: sources.length > 0, title: "Add your first source", sub: "Any public channel, @handle, playlist or video", to: "/sources?add=1" },
    { done: !!acct.connected, title: "Connect YouTube (optional)", sub: "Needed only for uploading", to: "/settings?tab=publishing" },
  ];
  const done = steps.filter((s) => s.done).length;
  if (dismissed || done === steps.length) return null;
  return (
    <motion.div className="card" style={{ marginBottom: 14, overflow: "hidden" }} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}>
      <div className="card-header">
        <span className="card-title">Get started</span>
        <span className="card-sub">{done} of {steps.length} done</span>
        <div style={{ width: 140 }}><Progress value={done / steps.length} thin /></div>
        <Button size="sm" variant="ghost" icon className="right" title="Hide"
          onClick={() => { setDismissed(true); try { localStorage.setItem(KEY, "1"); } catch { /* storage unavailable */ } }}><X size={14} /></Button>
      </div>
      <div className="grid grid-4" style={{ gap: 0 }}>
        {steps.map((s, i) => (
          <button key={s.title} onClick={() => nav(s.to)} className="row"
            style={{ gap: 10, padding: "14px 18px", textAlign: "left", background: "transparent", border: "none", cursor: "pointer",
              borderLeft: i ? "1px solid var(--border)" : "none", opacity: s.done ? 0.55 : 1 }}>
            {s.done ? <CheckCircle2 size={18} color="var(--success)" /> : <Circle size={18} color="var(--text-3)" />}
            <div className="grow">
              <div className="strong small">{s.title}</div>
              <div className="tiny faint">{s.sub}</div>
            </div>
            {!s.done && <ChevronRight size={14} color="var(--text-3)" />}
          </button>
        ))}
      </div>
    </motion.div>
  );
}
