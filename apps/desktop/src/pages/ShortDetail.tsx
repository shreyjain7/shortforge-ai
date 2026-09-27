import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle, ArrowLeft, CalendarClock, CheckCircle2, FolderOpen, RefreshCw, Scissors, Sparkles, Trash2, Upload, Wand2, XCircle,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { PhonePlayer, SafeToggle } from "../components/media";
import { Badge, Button, Modal, Progress, ScoreRing, Skeleton, StatusBadge } from "../components/ui";
import { api } from "../lib/api";
import { useEvents, useToast } from "../lib/events";
import { fmtBytes, fmtDateTime, fmtDuration, fmtTimecode, titleCase } from "../lib/format";

const MODES = ["clean", "high_curiosity", "educational", "funny", "tech", "podcast", "documentary", "energetic"];

export default function ShortDetail() {
  const { id } = useParams();
  const sid = Number(id);
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const { progress } = useEvents();
  const [safe, setSafe] = useState(false);
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const [when, setWhen] = useState("");
  const [visibility, setVisibility] = useState("public");
  const { data: s } = useQuery({ queryKey: ["short", sid], queryFn: () => api.short(sid), refetchInterval: 5000 });
  const { data: presets } = useQuery({ queryKey: ["presets"], queryFn: api.captionPresets });
  const { data: slots } = useQuery({ queryKey: ["slots"], queryFn: () => api.schedulePreview(4), enabled: scheduleOpen });
  const { data: account } = useQuery({ queryKey: ["youtube-account"], queryFn: api.youtubeAccount, enabled: scheduleOpen });
  const [title, setTitle] = useState("");
  const [desc, setDesc] = useState("");
  const [tags, setTags] = useState("");
  useEffect(() => {
    if (s) { setTitle(s.title); setDesc(s.description ?? ""); setTags((s.hashtags ?? []).join(" ")); }
  }, [s?.id, s?.updated_at]); // eslint-disable-line react-hooks/exhaustive-deps
  const refresh = () => { void qc.invalidateQueries({ queryKey: ["short", sid] }); void qc.invalidateQueries({ queryKey: ["shorts"] }); };
  const onErr = (e: Error) => toast({ title: "Action failed", body: e.message, level: "error" });
  const saveMeta = useMutation({
    mutationFn: () => api.patchShort(sid, { title, description: desc, hashtags: tags.split(/\s+/).filter(Boolean).map((t) => (t.startsWith("#") ? t : `#${t}`)) }),
    onSuccess: () => { toast({ title: "Saved", level: "success" }); refresh(); }, onError: onErr,
  });
  const rerender = useMutation({ mutationFn: (rebuild: boolean) => api.renderShort(sid, rebuild), onSuccess: () => { toast({ title: "Render queued", level: "info" }); refresh(); }, onError: onErr });
  const preset = useMutation({ mutationFn: (name: string) => api.patchShort(sid, { caption_preset: name }),
    onSuccess: () => rerender.mutate(false), onError: onErr });
  const regen = useMutation({ mutationFn: (mode: string) => api.regenerateMetadata(sid, mode), onSuccess: () => toast({ title: "Generating new titles…", level: "info" }), onError: onErr });
  const approve = useMutation({ mutationFn: () => api.patchShort(sid, { status: "ready" }), onSuccess: refresh });
  const schedule = useMutation({
    mutationFn: () => api.scheduleShort(sid, when ? new Date(when).toISOString() : null, visibility),
    onSuccess: () => { toast({ title: "Scheduled", body: "The upload job is in the queue.", level: "success" }); setScheduleOpen(false); refresh(); },
    onError: onErr,
  });
  const remove = useMutation({ mutationFn: () => api.deleteShort(sid, false), onSuccess: () => { toast({ title: "Short deleted", level: "info" }); nav("/shorts"); } });

  if (!s) return <div className="page"><Skeleton h={500} /></div>;
  const live = Object.values(progress).find((p) => p.short_id === sid);
  const qc_ = s.qc_report;
  return (
    <div className="page">
      <div className="row" style={{ marginBottom: 14 }}>
        <Button variant="ghost" size="sm" onClick={() => nav(-1)}><ArrowLeft size={14} /> Back</Button>
        <StatusBadge status={s.status} />
        {s.qc_status && <StatusBadge status={s.qc_status} />}
        <span className="tiny faint">#{s.id} · created {fmtDateTime(s.created_at)}</span>
      </div>
      <div className="grid" style={{ gridTemplateColumns: "auto minmax(0, 1fr)", gap: 26, alignItems: "start" }}>
        <div className="col" style={{ gap: 10 }}>
          <PhonePlayer src={s.video_url} poster={s.cover_url} showSafe={safe} width={340} safeArea={s.timeline?.safe_area} />
          <div className="row"><SafeToggle on={safe} onChange={setSafe} />
            {s.output_path && <Button size="sm" variant="ghost" className="right" onClick={() => api.reveal(s.output_path!)}><FolderOpen size={13} /> Show file</Button>}
          </div>
          {live && <div className="card card-pad"><div className="small">{live.message}</div><div style={{ marginTop: 6 }}><Progress value={live.progress} /></div></div>}
        </div>

        <div className="col" style={{ gap: 14, minWidth: 0 }}>
          <div className="card card-pad">
            <div className="row" style={{ gap: 14, alignItems: "flex-start" }}>
              <ScoreRing score={s.score} size={58} stroke={5} />
              <div className="grow">
                <input className="input" style={{ fontSize: 16, fontWeight: 600, height: 40 }} value={title} onChange={(e) => setTitle(e.target.value)} />
                <div className="tiny faint" style={{ marginTop: 6 }}>
                  {s.video?.title} · {fmtTimecode(s.start)} → {fmtTimecode(s.end)} · {fmtDuration(s.duration)}
                  {s.file_size ? ` · ${fmtBytes(s.file_size)}` : ""} · {s.width}×{s.height}
                </div>
              </div>
            </div>
            {s.title_options.length > 1 && (
              <div className="row-wrap" style={{ marginTop: 10, gap: 6 }}>
                <span className="tiny faint">Title options:</span>
                {s.title_options.map((t) => <button key={t} className={`chip ${t === title ? "on" : ""}`} onClick={() => setTitle(t)}>{t}</button>)}
              </div>
            )}
            <label className="label" style={{ marginTop: 12 }}>Description</label>
            <textarea className="textarea" rows={3} value={desc} onChange={(e) => setDesc(e.target.value)} />
            <label className="label" style={{ marginTop: 10 }}>Hashtags</label>
            <input className="input" value={tags} onChange={(e) => setTags(e.target.value)} />
            <div className="row" style={{ marginTop: 12, gap: 6 }}>
              <Button variant="primary" size="sm" loading={saveMeta.isPending} onClick={() => saveMeta.mutate()}>Save metadata</Button>
              <select className="select" style={{ width: 170, height: 26 }} defaultValue="" onChange={(e) => e.target.value && regen.mutate(e.target.value)}>
                <option value="">Regenerate titles as…</option>
                {MODES.map((m) => <option key={m} value={m}>{titleCase(m)}</option>)}
              </select>
              {s.metadata_source && <Badge color={s.metadata_source === "llm" ? "violet" : ""}>{s.metadata_source === "llm" ? `AI · ${s.metadata?.model}` : "extractive"}</Badge>}
            </div>
          </div>

          <div className="row-wrap" style={{ gap: 8 }}>
            {s.status === "review" && <Button variant="primary" onClick={() => approve.mutate()}><CheckCircle2 size={14} /> Approve</Button>}
            <Button variant={s.status === "ready" ? "primary" : "default"} disabled={!s.video_url || s.qc_status === "FAIL"} onClick={() => setScheduleOpen(true)}>
              <CalendarClock size={14} /> Schedule / upload</Button>
            <Button onClick={() => nav(`/editor/${s.id}`)}><Scissors size={14} /> Open in editor</Button>
            <Button onClick={() => rerender.mutate(false)} loading={rerender.isPending && rerender.variables === false}><RefreshCw size={14} /> Re-render</Button>
            <Button onClick={() => rerender.mutate(true)} title="Re-plan reframing, captions and pacing from scratch"><Wand2 size={14} /> Rebuild edit</Button>
            <select className="select" style={{ width: 170 }} value={s.caption_preset ?? ""} onChange={(e) => preset.mutate(e.target.value)}>
              {presets?.map((p) => <option key={p.name} value={p.name}>Captions: {p.name}</option>)}
            </select>
            <Button variant="danger" icon className="right" title="Delete Short" onClick={() => confirm("Delete this Short? The rendered file stays on disk.") && remove.mutate()}><Trash2 size={14} /></Button>
          </div>

          {s.error && <div className="card card-pad small" style={{ color: s.status === "failed" ? "var(--danger)" : "var(--warning)" }}>{s.error}</div>}

          <div className="grid grid-2">
            <div className="card">
              <div className="card-header"><Sparkles size={15} /><span className="card-title">Quality control</span>
                {qc_ && <span className="right"><StatusBadge status={qc_.status} /></span>}</div>
              <div className="card-pad col" style={{ gap: 8 }}>
                {!qc_ && <div className="faint small">Runs automatically after rendering.</div>}
                {qc_?.issues.length === 0 && <div className="row small" style={{ color: "var(--success)" }}><CheckCircle2 size={15} /> All {qc_.checks_run.length} checks passed</div>}
                {qc_?.issues.map((i, k) => (
                  <div key={k} className="row small" style={{ alignItems: "flex-start", gap: 8 }}>
                    {i.level === "FAIL" ? <XCircle size={15} color="var(--danger)" /> : <AlertTriangle size={15} color="var(--warning)" />}
                    <div><div>{i.message}</div><div className="tiny faint">{titleCase(i.check)}{i.repair ? ` · auto-repair: ${i.repair}` : ""}</div></div>
                  </div>
                ))}
                {qc_?.metrics && (
                  <div className="grid grid-2 tiny faint" style={{ gap: 4, marginTop: 6 }}>
                    <span>Loudness</span><span className="mono">{qc_.metrics.integrated_lufs ?? "—"} LUFS</span>
                    <span>True peak</span><span className="mono">{qc_.metrics.true_peak_db ?? "—"} dBTP</span>
                    <span>Crop jitter</span><span className="mono">{qc_.metrics.crop_jitter ?? "—"}</span>
                    <span>Face cut-off</span><span className="mono">{qc_.metrics.face_cut_ratio != null ? `${Math.round(qc_.metrics.face_cut_ratio * 100)}%` : "—"}</span>
                    <span>Output</span><span className="mono">{qc_.metrics.width}×{qc_.metrics.height} @ {qc_.metrics.fps}</span>
                  </div>
                )}
                {s.repair_attempts > 0 && <div className="tiny faint">Auto-repair attempts: {s.repair_attempts}</div>}
              </div>
            </div>
            <div className="card">
              <div className="card-header"><Wand2 size={15} /><span className="card-title">Edit decisions</span></div>
              <div className="card-pad small col" style={{ gap: 6 }}>
                {s.render_report?.reframe?.layouts && Object.entries(s.render_report.reframe.layouts as Record<string, number>).map(([k, v]) => (
                  <div key={k} className="row"><span className="muted">{titleCase(k)} layout</span><span className="right mono">{v.toFixed(1)}s</span></div>
                ))}
                <div className="row"><span className="muted">Jump cuts (silence trims)</span><span className="right mono">{Math.max(0, (s.render_report?.ranges ?? 1) - 1)}</span></div>
                <div className="row"><span className="muted">Dead air removed</span><span className="right mono">{s.render_report?.trimmed_s ?? 0}s</span></div>
                <div className="row"><span className="muted">Punch-ins</span><span className="right mono">{s.render_report?.punch_ins ?? 0}</span></div>
                <div className="row"><span className="muted">Caption style</span><span className="right">{s.caption_preset}</span></div>
                <div className="row"><span className="muted">Render</span><span className="right">{s.renders?.[s.renders.length - 1]?.encoder ?? "—"}
                  {s.renders?.[s.renders.length - 1]?.elapsed_s ? ` · ${s.renders[s.renders.length - 1].elapsed_s}s` : ""}</span></div>
              </div>
            </div>
          </div>

          {(s.uploads?.length ?? 0) > 0 && (
            <div className="card">
              <div className="card-header"><Upload size={15} /><span className="card-title">Uploads</span></div>
              <table className="table"><tbody>
                {s.uploads!.map((u) => (
                  <tr key={u.id}>
                    <td><StatusBadge status={u.status} /></td>
                    <td className="small">{u.publish_at ? `Publishes ${fmtDateTime(u.publish_at)}` : `Scheduled ${fmtDateTime(u.scheduled_at)}`}</td>
                    <td className="small">{u.visibility}</td>
                    <td className="small">{u.status === "uploading" ? <Progress value={u.progress} thin /> : u.url ? <a href={u.url} target="_blank" rel="noreferrer" className="gradient-text strong">Open on YouTube</a> : u.error}</td>
                  </tr>
                ))}
              </tbody></table>
            </div>
          )}
          {s.analytics && (
            <div className="card card-pad row" style={{ gap: 30 }}>
              <div><div className="tiny faint">Views</div><div className="strong" style={{ fontSize: 20 }}>{s.analytics.views ?? "—"}</div></div>
              <div><div className="tiny faint">Likes</div><div className="strong" style={{ fontSize: 20 }}>{s.analytics.likes ?? "—"}</div></div>
              <div><div className="tiny faint">Comments</div><div className="strong" style={{ fontSize: 20 }}>{s.analytics.comments ?? "—"}</div></div>
              <div className="tiny faint right">Updated {fmtDateTime(s.analytics.fetched_at)}</div>
            </div>
          )}
        </div>
      </div>

      <Modal open={scheduleOpen} onClose={() => setScheduleOpen(false)} title="Schedule upload"
        footer={<><Button variant="ghost" onClick={() => setScheduleOpen(false)}>Cancel</Button>
          <Button variant="primary" loading={schedule.isPending} disabled={!account?.connected} onClick={() => schedule.mutate()}><CalendarClock size={14} /> Schedule</Button></>}>
        {account && !account.connected && (
          <div className="card card-pad small" style={{ marginBottom: 14, borderColor: "rgba(251,191,36,.4)" }}>
            Connect a YouTube account in <a className="gradient-text strong" onClick={() => nav("/settings?tab=publishing")}>Settings → Publishing</a> to upload.
          </div>
        )}
        <label className="label">Publish time</label>
        <input className="input" type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} />
        <div className="row-wrap" style={{ marginTop: 8, gap: 6 }}>
          <span className="tiny faint">Next free slots:</span>
          {slots?.next_slots.map((t) => <button key={t} className="chip" onClick={() => setWhen(t.slice(0, 16))}>{fmtDateTime(t)}</button>)}
        </div>
        <div className="tiny faint" style={{ marginTop: 6 }}>Leave empty to use the next slot from your scheduling rules.</div>
        <label className="label" style={{ marginTop: 14 }}>Visibility</label>
        <select className="select" value={visibility} onChange={(e) => setVisibility(e.target.value)}>
          <option value="public">Public</option><option value="unlisted">Unlisted</option><option value="private">Private</option>
        </select>
      </Modal>
    </div>
  );
}
