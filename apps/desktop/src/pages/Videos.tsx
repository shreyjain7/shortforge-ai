import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { Download, Film, FolderInput, Search } from "lucide-react";
import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Button, Empty, Modal, PageHeader, Progress, Skeleton, StatusBadge, stagger } from "../components/ui";
import { api, mediaUrl } from "../lib/api";
import { useEvents, useToast } from "../lib/events";
import { fmtDuration, relTime } from "../lib/format";
import type { Video } from "../lib/types";

const FILTERS = [
  { v: "", l: "All" }, { v: "processing", l: "Processing" }, { v: "analyzed", l: "Analyzed" }, { v: "new", l: "Not processed" },
  { v: "ignored", l: "Skipped" },
];

export function VideoStage({ v }: { v: Video }) {
  const { progress } = useEvents();
  const live = Object.values(progress).find((p) => p.video_id === v.id && !p.short_id);
  if (live) {
    return (
      <div style={{ minWidth: 140 }}>
        <Progress value={live.progress} thin />
        <div className="tiny faint ellipsis" style={{ marginTop: 3, maxWidth: 220 }}>{live.message}</div>
      </div>
    );
  }
  if (v.processing_status === "analyzed") return <StatusBadge status="analyzed" />;
  if (v.download_status === "failed") return <StatusBadge status="failed" />;
  return <StatusBadge status={v.stage ? "processing" : v.processing_status} />;
}

export default function Videos() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState("");
  const [importOpen, setImportOpen] = useState(false);
  const [path, setPath] = useState("");
  const status = params.get("status") ?? "";
  const sourceId = params.get("source_id") ?? "";
  const { data, isLoading } = useQuery({
    queryKey: ["videos", status, sourceId, q],
    queryFn: () => api.videos({ status, source_id: sourceId, q, limit: 120 }),
    refetchInterval: 8000,
  });
  const process = useMutation({
    mutationFn: (id: number) => api.processVideo(id),
    onSuccess: () => { toast({ title: "Processing queued", level: "info" }); void qc.invalidateQueries({ queryKey: ["videos"] }); },
    onError: (e: Error) => toast({ title: "Could not queue", body: e.message, level: "error" }),
  });
  const importMut = useMutation({
    mutationFn: () => api.importLocal(path),
    onSuccess: (r) => { setImportOpen(false); setPath(""); nav(`/videos/${r.video_id}`); },
    onError: (e: Error) => toast({ title: "Import failed", body: e.message, level: "error" }),
  });
  return (
    <div className="page">
      <PageHeader title="Videos" subtitle={`${data?.total ?? 0} source videos`}
        actions={<Button onClick={() => setImportOpen(true)}><FolderInput size={15} /> Import local file</Button>} />
      <div className="toolbar">
        {FILTERS.map((f) => (
          <button key={f.v} className={`chip ${status === f.v ? "on" : ""}`}
            onClick={() => setParams({ ...(sourceId ? { source_id: sourceId } : {}), ...(f.v ? { status: f.v } : {}) })}>{f.l}</button>
        ))}
        {sourceId && <button className="chip on" onClick={() => setParams(status ? { status } : {})}>Source #{sourceId} ✕</button>}
        <div className="right" style={{ position: "relative", width: 260 }}>
          <Search size={14} style={{ position: "absolute", left: 10, top: 10, color: "var(--text-3)" }} />
          <input className="input" style={{ paddingLeft: 30 }} placeholder="Search titles or channels" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      </div>
      <div className="card">
        {isLoading ? <div className="card-pad col">{[0, 1, 2, 3].map((i) => <Skeleton key={i} h={54} />)}</div> : data && data.items.length === 0 ? (
          <Empty icon={<Film size={22} />} title="No videos">Add a source or import a local file to get started.</Empty>
        ) : (
          <table className="table">
            <thead><tr><th style={{ width: 150 }}></th><th>Video</th><th>Duration</th><th>Published</th><th>Candidates</th><th>Shorts</th><th>Status</th><th></th></tr></thead>
            <tbody>
              {data?.items.map((v, i) => (
                <motion.tr key={v.id} {...stagger(i)} style={{ cursor: "pointer" }} onClick={() => nav(`/videos/${v.id}`)}>
                  <td><div className="thumb" style={{ width: 132 }}>{v.thumbnail_url && <img src={mediaUrl(v.thumbnail_url)} loading="lazy" />}
                    <span className="duration">{fmtDuration(v.duration_s)}</span></div></td>
                  <td style={{ maxWidth: 420 }}>
                    <div className="strong clamp-2">{v.title}</div>
                    <div className="tiny faint">{v.channel_name ?? (v.youtube_id ? "YouTube" : "Local file")}</div>
                    {v.error && <div className="tiny" style={{ color: v.processing_status === "ignored" ? "var(--text-3)" : "var(--danger)" }}>{v.error}</div>}
                  </td>
                  <td className="mono small">{fmtDuration(v.duration_s)}</td>
                  <td className="small muted">{relTime(v.published_at ?? v.discovered_at)}</td>
                  <td className="small">{typeof v.candidates === "number" ? v.candidates : 0}</td>
                  <td className="small">{typeof v.shorts === "number" ? v.shorts : 0}</td>
                  <td><VideoStage v={v} /></td>
                  <td onClick={(e) => e.stopPropagation()}>
                    {(v.processing_status === "new" || v.processing_status === "ignored" || v.download_status === "failed") && (
                      <Button size="sm" onClick={() => process.mutate(v.id)}><Download size={13} /> Process</Button>
                    )}
                  </td>
                </motion.tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <Modal open={importOpen} onClose={() => setImportOpen(false)} title="Import a local video"
        footer={<><Button variant="ghost" onClick={() => setImportOpen(false)}>Cancel</Button>
          <Button variant="primary" disabled={!path} loading={importMut.isPending} onClick={() => importMut.mutate()}>Import & process</Button></>}>
        <label className="label">Full path to a video file</label>
        <input className="input" placeholder="C:\Videos\podcast-episode.mp4" value={path} onChange={(e) => setPath(e.target.value)} autoFocus />
        <div className="tiny faint" style={{ marginTop: 8 }}>Files are processed in place (not copied). Supported: mp4, mkv, mov, webm, avi, m4v…</div>
      </Modal>
    </div>
  );
}
