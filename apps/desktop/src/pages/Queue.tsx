import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowUpToLine, ChevronRight, ListVideo, Pause, Play, RotateCcw, Trash2, X } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Button, Empty, Modal, PageHeader, Progress, Segmented, StatusBadge } from "../components/ui";
import { api } from "../lib/api";
import { useEvents, useToast } from "../lib/events";
import { fmtDateTime, relTime } from "../lib/format";
import type { Job } from "../lib/types";

function JobLogs({ id, onClose }: { id: number; onClose: () => void }) {
  const { data } = useQuery({ queryKey: ["job", id], queryFn: () => api.job(id), refetchInterval: 2000 });
  return (
    <Modal open onClose={onClose} title={data ? `${data.label} #${data.id}` : "Job"} wide>
      {data && (
        <div className="col" style={{ gap: 12 }}>
          <div className="row-wrap small" style={{ gap: 10 }}>
            <StatusBadge status={data.status} /><span className="muted">resource: {data.resource}</span><span className="muted">priority {data.priority}</span>
            <span className="muted">attempt {data.retry_count + 1}/{data.max_retries + 1}</span>
            <span className="muted">created {fmtDateTime(data.created_at)}</span>
            {data.finished_at && <span className="muted">finished {fmtDateTime(data.finished_at)}</span>}
          </div>
          {data.error && <div className="card card-pad small" style={{ color: "var(--danger)" }}>{data.error}</div>}
          {data.error_detail && <details><summary className="small muted" style={{ cursor: "pointer" }}>Technical details</summary><div className="log">{data.error_detail}</div></details>}
          {data.result && <div className="log">{JSON.stringify(data.result, null, 2)}</div>}
          <div className="log">{data.logs || "No log lines yet."}</div>
        </div>
      )}
    </Modal>
  );
}

function JobRow({ j, onOpen }: { j: Job; onOpen: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const { progress } = useEvents();
  const act = useMutation({
    mutationFn: (a: "cancel" | "retry" | "pause" | "resume" | "top") => api.jobAction(j.id, a),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["jobs"] }),
    onError: (e: Error) => toast({ title: "Action failed", body: e.message, level: "error" }),
  });
  const live = progress[j.id];
  const p = live?.progress ?? j.progress;
  const msg = live?.message ?? j.message;
  return (
    <motion.tr layout initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
      <td style={{ width: 110 }}><StatusBadge status={j.status} /></td>
      <td>
        <div className="row" style={{ gap: 8 }}><span className="strong">{j.label}</span><span className="tiny faint">#{j.id}</span>
          {j.video_id && <Link to={`/videos/${j.video_id}`} className="tiny faint">video {j.video_id}</Link>}
          {j.short_id && <Link to={`/shorts/${j.short_id}`} className="tiny faint">short {j.short_id}</Link>}
          {j.retry_count > 0 && <Badge color="yellow">retry {j.retry_count}</Badge>}
        </div>
        {j.status === "running" ? (
          <div style={{ marginTop: 6, maxWidth: 520 }}><Progress value={p} thin /><div className="tiny faint ellipsis" style={{ marginTop: 3 }}>{msg}</div></div>
        ) : (
          <div className="tiny" style={{ color: j.status === "failed" ? "var(--danger)" : "var(--text-3)", marginTop: 3 }}>
            {j.status === "failed" ? j.error : j.not_before && j.status === "queued" ? `${j.message ?? ""} · next attempt ${relTime(j.not_before)}` : msg}
          </div>
        )}
      </td>
      <td className="tiny faint">{j.resource}</td>
      <td className="tiny faint mono">{j.priority}</td>
      <td className="tiny faint">{relTime(j.finished_at ?? j.started_at ?? j.created_at)}</td>
      <td style={{ whiteSpace: "nowrap", textAlign: "right" }}>
        {j.status === "queued" && <Button size="sm" variant="ghost" icon title="Move to top" onClick={() => act.mutate("top")}><ArrowUpToLine size={13} /></Button>}
        {j.status === "queued" && <Button size="sm" variant="ghost" icon title="Pause" onClick={() => act.mutate("pause")}><Pause size={13} /></Button>}
        {j.status === "paused" && <Button size="sm" variant="ghost" icon title="Resume" onClick={() => act.mutate("resume")}><Play size={13} /></Button>}
        {(j.status === "failed" || j.status === "cancelled") && <Button size="sm" variant="ghost" icon title="Retry" onClick={() => act.mutate("retry")}><RotateCcw size={13} /></Button>}
        {["queued", "running", "paused"].includes(j.status) && <Button size="sm" variant="ghost" icon title="Cancel" onClick={() => act.mutate("cancel")}><X size={13} /></Button>}
        <Button size="sm" variant="ghost" icon title="Details & logs" onClick={onOpen}><ChevronRight size={14} /></Button>
      </td>
    </motion.tr>
  );
}

export default function Queue() {
  const qc = useQueryClient();
  const [view, setView] = useState<"active" | "failed" | "done" | "all">("active");
  const [open, setOpen] = useState<number | null>(null);
  const status = { active: "queued,running,paused", failed: "failed", done: "done,cancelled", all: "" }[view];
  const { data } = useQuery({ queryKey: ["jobs", view], queryFn: () => api.jobs({ status, limit: 300 }), refetchInterval: 4000 });
  const pause = useMutation({ mutationFn: (p: boolean) => api.pauseQueue(p), onSuccess: () => { void qc.invalidateQueries({ queryKey: ["jobs"] }); void qc.invalidateQueries({ queryKey: ["stats"] }); } });
  const clear = useMutation({ mutationFn: api.clearFinished, onSuccess: () => void qc.invalidateQueries({ queryKey: ["jobs"] }) });
  const c = data?.counts ?? {};
  return (
    <div className="page">
      <PageHeader title="Queue" subtitle="Persistent jobs — they survive restarts, retry temporary failures and never block each other."
        actions={<>
          <Button onClick={() => clear.mutate()}><Trash2 size={14} /> Clear finished</Button>
          <Button variant={data?.paused ? "primary" : "default"} onClick={() => pause.mutate(!data?.paused)}>
            {data?.paused ? <><Play size={14} /> Resume queue</> : <><Pause size={14} /> Pause queue</>}
          </Button>
        </>} />
      <div className="card card-pad" style={{ marginBottom: 14 }}>
        <div className="row-wrap" style={{ gap: 6 }}>
          {data?.stages.map((s, i) => (
            <div key={s.type} className="row" style={{ gap: 6 }}>
              <div className="chip" style={{ cursor: "default", borderColor: s.active ? "rgba(34,211,238,.5)" : undefined, color: s.active ? "var(--text)" : undefined }}>
                {s.label}{s.active ? <b style={{ color: "var(--accent-2)" }}>{s.active}</b> : null}
              </div>
              {i < (data?.stages.length ?? 0) - 1 && <ChevronRight size={12} color="var(--text-3)" />}
            </div>
          ))}
        </div>
      </div>
      <div className="toolbar">
        <Segmented value={view} onChange={setView} options={[
          { value: "active", label: `Active · ${(c.queued ?? 0) + (c.running ?? 0) + (c.paused ?? 0)}` },
          { value: "failed", label: `Failed · ${c.failed ?? 0}` }, { value: "done", label: `Completed · ${c.done ?? 0}` }, { value: "all", label: "All" }]} />
      </div>
      <div className="card">
        {data && data.items.length === 0 ? <Empty icon={<ListVideo size={22} />} title="Nothing here">Jobs appear when sources are scanned, videos processed or Shorts rendered.</Empty> : (
          <table className="table">
            <thead><tr><th>Status</th><th>Job</th><th>Resource</th><th>Priority</th><th>When</th><th></th></tr></thead>
            <tbody><AnimatePresence initial={false}>{data?.items.map((j) => <JobRow key={j.id} j={j} onOpen={() => setOpen(j.id)} />)}</AnimatePresence></tbody>
          </table>
        )}
      </div>
      {open !== null && <JobLogs id={open} onClose={() => setOpen(null)} />}
    </div>
  );
}
