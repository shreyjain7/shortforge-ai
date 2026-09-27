import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { motion } from "framer-motion";
import {
  AlertCircle, FileVideo, Film, ListVideo, Pause, Play, Plus, Radio, RefreshCw, Search, Settings2, Trash2, User, Video,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Badge, Button, Empty, FieldRow, Modal, PageHeader, Skeleton, stagger, Toggle } from "../components/ui";
import { api } from "../lib/api";
import { useToast } from "../lib/events";
import { fmtCompact, fmtDuration, relTime } from "../lib/format";
import type { ResolvePreview, Source } from "../lib/types";

const KIND_ICON: Record<string, React.ReactNode> = {
  channel: <Radio size={13} />, handle: <User size={13} />, video: <Video size={13} />, playlist: <ListVideo size={13} />,
  local_file: <FileVideo size={13} />,
};

function detectKind(text: string): string | null {
  const t = text.trim();
  if (!t) return null;
  if (/^[a-z]:[\\/]|^\\\\|^\//i.test(t)) return "local_file";
  if (t.startsWith("@") || /youtube\.com\/@/.test(t)) return "handle";
  if (/list=/.test(t) && !/watch\?v=/.test(t)) return "playlist";
  if (/watch\?v=|youtu\.be\/|\/shorts\/|\/live\//.test(t)) return "video";
  if (/\/channel\/|\/c\/|\/user\/|^UC[\w-]{22}$/.test(t)) return "channel";
  return null;
}

function AddSourceModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [input, setInput] = useState("");
  const [preview, setPreview] = useState<ResolvePreview | null>(null);
  const [opts, setOpts] = useState({ max_videos_per_scan: 3, max_video_age_days: 30, min_duration_s: 180, auto_scan: true, process_existing: true, priority: 50 });
  const resolve = useMutation({
    mutationFn: () => api.resolveSource(input),
    onSuccess: setPreview,
    onError: (e: Error) => { setPreview(null); toast({ title: "Could not resolve source", body: e.message, level: "error" }); },
  });
  const add = useMutation({
    mutationFn: () => api.addSource({ input, ...opts, scan_now: true }),
    onSuccess: (s) => {
      toast({ title: "Source added", body: `${s.title} — scanning for videos now.`, level: "success" });
      void qc.invalidateQueries({ queryKey: ["sources"] });
      setInput(""); setPreview(null); onClose();
    },
    onError: (e: Error) => toast({ title: "Could not add source", body: e.message, level: "error" }),
  });
  const kind = detectKind(input);
  const isVideo = kind === "video" || preview?.kind === "video";
  return (
    <Modal open={open} onClose={onClose} title="Add source" wide
      footer={<>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button variant="primary" loading={add.isPending} disabled={!input.trim()} onClick={() => add.mutate()}>
          <Plus size={14} /> {isVideo ? "Add & process video" : "Add & scan"}
        </Button>
      </>}>
      <div className="row" style={{ gap: 10 }}>
        <div className="grow" style={{ position: "relative" }}>
          <input className="input input-lg" autoFocus placeholder="@handle, channel URL, video URL, playlist URL or C:\path\to\video.mp4"
            value={input} onChange={(e) => { setInput(e.target.value); setPreview(null); }}
            onKeyDown={(e) => e.key === "Enter" && input.trim() && resolve.mutate()} />
          {kind && <span className="badge violet" style={{ position: "absolute", right: 10, top: 12 }}>{KIND_ICON[kind]} {kind.replace("_", " ")}</span>}
        </div>
        <Button size="lg" onClick={() => resolve.mutate()} loading={resolve.isPending} disabled={!input.trim()}><Search size={15} /> Preview</Button>
      </div>
      <div className="tiny faint" style={{ marginTop: 8 }}>
        Works with any public channel — no allow-lists. The input type is detected automatically.
      </div>
      {resolve.isPending && <div className="card card-pad" style={{ marginTop: 16 }}><Skeleton h={60} /></div>}
      {preview && (
        <motion.div className="card" style={{ marginTop: 16, overflow: "hidden" }} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}>
          <div className="row card-pad" style={{ gap: 14 }}>
            {preview.thumbnail_url && <img src={preview.thumbnail_url} style={{ width: 56, height: 56, borderRadius: preview.channel ? 56 : 10, objectFit: "cover" }} />}
            <div className="grow">
              <div className="strong" style={{ fontSize: 15 }}>{preview.title}</div>
              <div className="small muted row-wrap" style={{ marginTop: 3 }}>
                <Badge color="violet">{preview.kind}</Badge>
                {preview.channel?.handle && <span>{preview.channel.handle}</span>}
                {preview.channel?.subscriber_count != null && <span>{fmtCompact(preview.channel.subscriber_count)} subscribers</span>}
                {preview.video_count != null && <span>{fmtCompact(preview.video_count)} videos</span>}
                {preview.channel?.id && <span className="mono faint">{preview.channel.id}</span>}
              </div>
            </div>
          </div>
          {preview.recent_videos.length > 0 && (
            <div style={{ padding: "0 18px 16px" }}>
              <div className="tiny faint" style={{ marginBottom: 8 }}>{preview.kind === "video" ? "Video" : "Recent uploads"}</div>
              <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))", gap: 10 }}>
                {preview.recent_videos.slice(0, 8).map((v) => (
                  <div key={v.id}>
                    <div className="thumb">{v.thumbnail_url && <img src={v.thumbnail_url} />}<span className="duration">{fmtDuration(v.duration)}</span></div>
                    <div className="tiny clamp-2" style={{ marginTop: 5 }}>{v.title}</div>
                    <div className="tiny faint">{v.published_at ? relTime(v.published_at) : ""}</div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </motion.div>
      )}
      {!isVideo && (
        <div className="card card-pad" style={{ marginTop: 16 }}>
          <div className="strong" style={{ marginBottom: 4 }}>Ingestion rules</div>
          <FieldRow label="Videos per scan" desc="How many new uploads to download and process each scan">
            <input className="input" type="number" min={1} max={50} value={opts.max_videos_per_scan} style={{ width: 90 }}
              onChange={(e) => setOpts({ ...opts, max_videos_per_scan: Number(e.target.value) })} />
          </FieldRow>
          <FieldRow label="Maximum video age" desc="Ignore uploads older than this (days)">
            <input className="input" type="number" min={1} value={opts.max_video_age_days} style={{ width: 90 }}
              onChange={(e) => setOpts({ ...opts, max_video_age_days: Number(e.target.value) })} />
          </FieldRow>
          <FieldRow label="Minimum source duration" desc="Skip videos shorter than this (seconds)">
            <input className="input" type="number" min={0} value={opts.min_duration_s} style={{ width: 90 }}
              onChange={(e) => setOpts({ ...opts, min_duration_s: Number(e.target.value) })} />
          </FieldRow>
          <FieldRow label="Process existing uploads" desc="Also process recent videos already on the channel, not only new ones">
            <Toggle on={opts.process_existing} onChange={(v) => setOpts({ ...opts, process_existing: v })} />
          </FieldRow>
          <FieldRow label="Automatic scanning" desc="Re-scan on the Autopilot interval when Autopilot is on">
            <Toggle on={opts.auto_scan} onChange={(v) => setOpts({ ...opts, auto_scan: v })} />
          </FieldRow>
        </div>
      )}
    </Modal>
  );
}

function SourceSettingsModal({ source, onClose }: { source: Source | null; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const { data: presets } = useQuery({ queryKey: ["presets"], queryFn: api.captionPresets });
  const [form, setForm] = useState<Partial<Source>>({});
  useEffect(() => { if (source) setForm(source); }, [source]);
  const save = useMutation({
    mutationFn: () => {
      const keys = ["enabled", "auto_scan", "priority", "scan_interval_min", "max_videos_per_scan", "min_duration_s", "max_duration_s",
        "max_video_age_days", "max_shorts_per_video", "max_shorts_per_day", "min_candidate_score", "short_min_s", "short_max_s",
        "preferred_style", "reframe_mode", "schedule_strategy", "auto_download", "process_existing"] as const;
      const body: Record<string, unknown> = {};
      const clear: string[] = [];
      for (const k of keys) {
        const v = form[k];
        if (v === null || v === "" || (typeof v === "number" && isNaN(v))) clear.push(k);
        else body[k] = v;
      }
      return api.patchSource(source!.id, { ...body, clear });
    },
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["sources"] }); toast({ title: "Source updated", level: "success" }); onClose(); },
    onError: (e: Error) => toast({ title: "Update failed", body: e.message, level: "error" }),
  });
  if (!source) return null;
  const num = (k: keyof Source, placeholder: string) => (
    <input className="input" type="number" style={{ width: 110 }} placeholder={placeholder}
      value={(form[k] as number | null | undefined) ?? ""}
      onChange={(e) => setForm({ ...form, [k]: e.target.value === "" ? null : Number(e.target.value) })} />
  );
  return (
    <Modal open={!!source} onClose={onClose} title={`${source.title} · rules`} wide
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" loading={save.isPending} onClick={() => save.mutate()}>Save</Button></>}>
      <div className="grid grid-2" style={{ gap: 24 }}>
        <div>
          <div className="strong" style={{ marginBottom: 6 }}>Monitoring</div>
          <FieldRow label="Enabled"><Toggle on={!!form.enabled} onChange={(v) => setForm({ ...form, enabled: v })} /></FieldRow>
          <FieldRow label="Automatic scan"><Toggle on={!!form.auto_scan} onChange={(v) => setForm({ ...form, auto_scan: v })} /></FieldRow>
          <FieldRow label="Auto-download new videos"><Toggle on={!!form.auto_download} onChange={(v) => setForm({ ...form, auto_download: v })} /></FieldRow>
          <FieldRow label="Priority" desc="0–100, higher is processed first">{num("priority", "50")}</FieldRow>
          <FieldRow label="Scan frequency" desc="Minutes (blank = global)">{num("scan_interval_min", "global")}</FieldRow>
          <FieldRow label="Videos per scan">{num("max_videos_per_scan", "3")}</FieldRow>
          <FieldRow label="Video-age window" desc="Days">{num("max_video_age_days", "any")}</FieldRow>
          <FieldRow label="Min source duration" desc="Seconds">{num("min_duration_s", "none")}</FieldRow>
          <FieldRow label="Max source duration" desc="Seconds">{num("max_duration_s", "none")}</FieldRow>
        </div>
        <div>
          <div className="strong" style={{ marginBottom: 6 }}>Shorts</div>
          <FieldRow label="Max Shorts per video" desc="Blank = global">{num("max_shorts_per_video", "global")}</FieldRow>
          <FieldRow label="Max Shorts per day">{num("max_shorts_per_day", "unlimited")}</FieldRow>
          <FieldRow label="Minimum candidate score">{num("min_candidate_score", "global")}</FieldRow>
          <FieldRow label="Short length (min s)">{num("short_min_s", "global")}</FieldRow>
          <FieldRow label="Short length (max s)">{num("short_max_s", "global")}</FieldRow>
          <FieldRow label="Caption style">
            <select className="select" value={form.preferred_style ?? ""} onChange={(e) => setForm({ ...form, preferred_style: e.target.value || null })}>
              <option value="">Global default</option>
              {presets?.map((p) => <option key={p.name}>{p.name}</option>)}
            </select>
          </FieldRow>
          <FieldRow label="Reframing mode">
            <select className="select" value={form.reframe_mode ?? ""} onChange={(e) => setForm({ ...form, reframe_mode: e.target.value || null })}>
              <option value="">Global default</option>
              {["auto", "speaker", "conversation", "podcast", "presentation", "tech", "gameplay", "product", "cinematic"].map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </FieldRow>
          <FieldRow label="Schedule strategy">
            <select className="select" value={form.schedule_strategy ?? ""} onChange={(e) => setForm({ ...form, schedule_strategy: e.target.value || null })}>
              <option value="">Global default</option><option value="slots">Daily slots</option><option value="interval">Minimum interval</option><option value="queue">Queue order</option>
            </select>
          </FieldRow>
        </div>
      </div>
    </Modal>
  );
}

function SourceCard({ s, i, onSettings }: { s: Source; i: number; onSettings: () => void }) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const toast = useToast();
  const refresh = () => void qc.invalidateQueries({ queryKey: ["sources"] });
  const scan = useMutation({ mutationFn: () => api.scanSource(s.id), onSuccess: () => { toast({ title: "Scan started", body: s.title, level: "info" }); refresh(); } });
  const toggle = useMutation({ mutationFn: () => api.patchSource(s.id, { enabled: !s.enabled }), onSuccess: refresh });
  const remove = useMutation({ mutationFn: () => api.deleteSource(s.id), onSuccess: () => { toast({ title: "Source removed", level: "info" }); refresh(); } });
  const avatar = s.channel?.avatar_url ?? s.thumbnail_url;
  return (
    <motion.div className="card" {...stagger(i)} whileHover={{ y: -2 }} style={{ overflow: "hidden", opacity: s.enabled ? 1 : 0.6 }}>
      <div style={{ height: 64, background: s.channel?.banner_url ? `center/cover url(${s.channel.banner_url})` : "var(--grad)", opacity: 0.55 }} />
      <div className="card-pad" style={{ marginTop: -38 }}>
        <div className="row" style={{ alignItems: "flex-end", gap: 12 }}>
          <div style={{ width: 58, height: 58, borderRadius: s.kind === "video" || s.kind === "playlist" ? 12 : 58, overflow: "hidden",
            border: "3px solid var(--bg-2)", background: "var(--solid-2)", flexShrink: 0, display: "grid", placeItems: "center" }}>
            {avatar ? <img src={avatar} style={{ width: "100%", height: "100%", objectFit: "cover" }} /> : KIND_ICON[s.kind]}
          </div>
          <div className="grow" style={{ paddingBottom: 2 }}>
            <div className="strong ellipsis" style={{ fontSize: 14.5 }}>{s.title}</div>
            <div className="tiny faint row" style={{ gap: 6 }}>
              {KIND_ICON[s.kind]} {s.channel?.handle ?? s.kind}
              {s.channel?.subscriber_count != null && <span>· {fmtCompact(s.channel.subscriber_count)} subs</span>}
            </div>
          </div>
          {s.status === "scanning" ? <Badge color="blue" dot>Scanning</Badge> : s.status === "error" ? <Badge color="red">Error</Badge> : !s.enabled ? <Badge>Paused</Badge> : null}
        </div>
        <div className="grid grid-3" style={{ gap: 8, marginTop: 14 }}>
          {[["Indexed", s.stats.videos], ["Processed", s.stats.processed], ["Shorts", s.stats.shorts]].map(([k, v]) => (
            <div key={k as string} style={{ background: "var(--surface)", borderRadius: 10, padding: "8px 10px", border: "1px solid var(--border)" }}>
              <div className="tiny faint">{k}</div><div className="strong" style={{ fontSize: 16 }}>{v}</div>
            </div>
          ))}
        </div>
        <div className="tiny faint" style={{ marginTop: 10, display: "flex", gap: 12, flexWrap: "wrap" }}>
          <span>Last checked {relTime(s.last_scan_at)}</span>
          {s.auto_scan && s.enabled && s.kind !== "video" && <span>Next scan {relTime(s.next_scan_at)}</span>}
          {s.stats.processing > 0 && <span style={{ color: "var(--accent-2)" }}>{s.stats.processing} processing</span>}
        </div>
        {s.last_error && <div className="tiny" style={{ color: "var(--danger)", marginTop: 8, display: "flex", gap: 6 }}><AlertCircle size={13} />{s.last_error}</div>}
        <div className="row" style={{ marginTop: 14, gap: 6 }}>
          <Button size="sm" onClick={() => scan.mutate()} loading={scan.isPending}><RefreshCw size={13} /> Scan</Button>
          <Button size="sm" onClick={() => nav(`/videos?source_id=${s.id}`)}><Film size={13} /> Videos</Button>
          <Button size="sm" variant="ghost" icon onClick={onSettings} title="Settings"><Settings2 size={14} /></Button>
          <Button size="sm" variant="ghost" icon onClick={() => toggle.mutate()} title={s.enabled ? "Pause" : "Resume"}>{s.enabled ? <Pause size={14} /> : <Play size={14} />}</Button>
          <Button size="sm" variant="ghost" icon className="right" title="Remove"
            onClick={() => confirm(`Remove ${s.title}? Processed videos and Shorts are kept.`) && remove.mutate()}><Trash2 size={14} /></Button>
        </div>
      </div>
    </motion.div>
  );
}

export default function Sources() {
  const [params, setParams] = useSearchParams();
  const [adding, setAdding] = useState(params.get("add") === "1");
  const [editing, setEditing] = useState<Source | null>(null);
  const { data, isLoading } = useQuery({ queryKey: ["sources"], queryFn: api.sources, refetchInterval: 8000 });
  return (
    <div className="page">
      <PageHeader title="Sources" subtitle="Any public YouTube channel, @handle, playlist, video — or local files."
        actions={<Button variant="primary" onClick={() => setAdding(true)}><Plus size={15} /> Add source</Button>} />
      {isLoading && <div className="grid grid-3">{[0, 1, 2].map((i) => <div key={i} className="card card-pad"><Skeleton h={140} /></div>)}</div>}
      {data && data.length === 0 && (
        <div className="card"><Empty icon={<Radio size={22} />} title="Add your first source"
          action={<Button variant="primary" onClick={() => setAdding(true)}><Plus size={14} /> Add source</Button>}>
          Paste a channel like <span className="kbd">@SomeCreator</span>, a channel/playlist/video URL, or a path to a local video file.
        </Empty></div>
      )}
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(330px, 1fr))" }}>
        {data?.map((s, i) => <SourceCard key={s.id} s={s} i={i} onSettings={() => setEditing(s)} />)}
      </div>
      <AddSourceModal open={adding} onClose={() => { setAdding(false); if (params.get("add")) setParams({}); }} />
      <SourceSettingsModal source={editing} onClose={() => setEditing(null)} />
    </div>
  );
}

