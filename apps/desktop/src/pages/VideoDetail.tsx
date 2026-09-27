import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Brain, ExternalLink, FileText, Play, RefreshCw, Sparkles, Wand2 } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { SemanticTimeline } from "../components/media";
import { Badge, Button, Empty, Modal, Progress, ScoreRing, Skeleton, StatusBadge } from "../components/ui";
import { api, mediaUrl } from "../lib/api";
import { useEvents, useToast } from "../lib/events";
import { fmtDuration, fmtTimecode, LABEL_COLORS, relTime, titleCase } from "../lib/format";
import type { Candidate } from "../lib/types";

export default function VideoDetail() {
  const { id } = useParams();
  const vid = Number(id);
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const { progress } = useEvents();
  const player = useRef<HTMLVideoElement>(null);
  const [t, setT] = useState(0);
  const [showTranscript, setShowTranscript] = useState(false);
  const { data: v } = useQuery({ queryKey: ["video", vid], queryFn: () => api.video(vid), refetchInterval: 6000 });
  const transcript = useQuery({ queryKey: ["transcript", vid], queryFn: () => api.transcript(vid), enabled: v?.transcript_status === "done" });
  const refresh = () => { void qc.invalidateQueries({ queryKey: ["video", vid] }); void qc.invalidateQueries({ queryKey: ["candidates"] }); };
  const act = (fn: () => Promise<unknown>, title: string) => ({
    mutationFn: fn,
    onSuccess: () => { toast({ title, level: "info" }); refresh(); },
    onError: (e: Error) => toast({ title: "Action failed", body: e.message, level: "error" }),
  });
  const analyze = useMutation(act(() => api.processVideo(vid), "Processing queued"));
  const reanalyze = useMutation(act(() => api.reanalyze(vid), "Re-analysis queued"));
  const findMore = useMutation(act(() => api.findMore(vid), "Looking for more clips"));
  const generate = useMutation(act(() => api.generateShorts(vid, 3), "Rendering top candidates"));
  const genOne = useMutation({
    mutationFn: (cid: number) => api.generateCandidate(cid),
    onSuccess: (r) => { toast({ title: "Short queued for rendering", level: "success", link: `/shorts/${r.short_id}` }); refresh(); },
  });
  const candidates = (Array.isArray(v?.candidates) ? v.candidates : []) as Candidate[];
  const live = Object.entries(progress).filter(([, p]) => p.video_id === vid && !p.short_id);
  const activeSeg = useMemo(() => v?.timeline?.find((s) => t >= s.start && t < s.end), [v?.timeline, t]);
  const seek = (time: number) => { if (player.current) { player.current.currentTime = time; void player.current.play(); } };

  if (!v) return <div className="page"><Skeleton h={320} /></div>;
  const duration = v.duration_s ?? 1;
  return (
    <div className="page">
      <div className="row" style={{ marginBottom: 14 }}>
        <Button variant="ghost" size="sm" onClick={() => nav(-1)}><ArrowLeft size={14} /> Back</Button>
      </div>
      <div className="row" style={{ alignItems: "flex-start", gap: 18, marginBottom: 16 }}>
        <div className="grow">
          <h1 style={{ margin: 0, fontSize: 20, letterSpacing: "-0.02em" }}>{v.title}</h1>
          <div className="row-wrap small muted" style={{ marginTop: 6 }}>
            <span>{v.channel_name ?? "Local file"}</span>
            <span>· {fmtDuration(v.duration_s)}</span>
            {v.width && <span>· {v.width}×{v.height} @ {v.fps?.toFixed(2)} fps</span>}
            {v.language && <Badge>{v.language}</Badge>}
            {v.published_at && <span>· published {relTime(v.published_at)}</span>}
            {v.source_url?.startsWith("http") && <a href={v.source_url} target="_blank" rel="noreferrer" className="row" style={{ gap: 4 }}>YouTube <ExternalLink size={12} /></a>}
          </div>
        </div>
        <div className="row-wrap" style={{ gap: 6 }}>
          {v.processing_status !== "analyzed" ? (
            <Button variant="primary" loading={analyze.isPending} onClick={() => analyze.mutate()}><Brain size={14} /> Analyze</Button>
          ) : (
            <>
              <Button onClick={() => reanalyze.mutate()} loading={reanalyze.isPending}><RefreshCw size={14} /> Reanalyze</Button>
              <Button onClick={() => findMore.mutate()} loading={findMore.isPending}><Sparkles size={14} /> Find more clips</Button>
              <Button variant="primary" onClick={() => generate.mutate()} loading={generate.isPending}><Wand2 size={14} /> Generate Shorts</Button>
            </>
          )}
          <Button disabled={v.transcript_status !== "done"} onClick={() => setShowTranscript(true)}><FileText size={14} /> Transcript</Button>
        </div>
      </div>

      {live.length > 0 && (
        <div className="card card-pad col" style={{ marginBottom: 14, gap: 10 }}>
          {live.map(([jid, p]) => (
            <div key={jid}><div className="row small"><span className="strong">{p.message}</span><span className="right mono faint">{Math.round(p.progress * 100)}%</span></div>
              <div style={{ marginTop: 6 }}><Progress value={p.progress} /></div></div>
          ))}
        </div>
      )}
      {v.error && <div className="card card-pad small" style={{ marginBottom: 14, color: "var(--danger)" }}>{v.error}</div>}

      <div className="grid" style={{ gridTemplateColumns: "minmax(0, 1.55fr) minmax(320px, 1fr)", alignItems: "start" }}>
        <div className="col" style={{ gap: 14 }}>
          <div className="card" style={{ overflow: "hidden" }}>
            {v.proxy_url ? (
              <video ref={player} src={mediaUrl(v.proxy_url)} poster={mediaUrl(v.thumbnail_url)} preload="metadata" controls style={{ width: "100%", display: "block", background: "#000", aspectRatio: "16/9" }}
                onTimeUpdate={(e) => setT(e.currentTarget.currentTime)} />
            ) : (
              <div className="thumb" style={{ borderRadius: 0 }}>{v.thumbnail_url && <img src={mediaUrl(v.thumbnail_url)} />}</div>
            )}
            <div className="card-pad">
              <div className="row small" style={{ marginBottom: 8 }}>
                <span className="strong">Semantic timeline</span>
                {v.analysis?.timeline_source && <Badge color={v.analysis.timeline_source === "llm" ? "violet" : ""}>
                  {v.analysis.timeline_source === "llm" ? `AI · ${v.analysis.llm_model}` : "heuristic"}</Badge>}
                <span className="right faint mono">{fmtTimecode(t)}</span>
              </div>
              {v.timeline && v.timeline.length > 0 ? (
                <SemanticTimeline duration={duration} segments={v.timeline} cuts={v.scene_cuts} candidates={candidates} playhead={t}
                  onSeek={seek} onCandidate={(c) => seek(c.start)} />
              ) : <div className="faint small">Available after analysis.</div>}
              {activeSeg && (
                <div className="row small" style={{ marginTop: 10, gap: 8 }}>
                  <span className="badge" style={{ background: LABEL_COLORS[activeSeg.label], color: "#fff" }}>{titleCase(activeSeg.label)}</span>
                  <span className="muted">{activeSeg.summary}</span>
                </div>
              )}
              <div className="row-wrap tiny faint" style={{ marginTop: 10, gap: 10 }}>
                {Object.entries(LABEL_COLORS).filter(([k]) => v.timeline?.some((s) => s.label === k)).map(([k, c]) => (
                  <span key={k} className="row" style={{ gap: 4 }}><span style={{ width: 8, height: 8, borderRadius: 2, background: c }} />{titleCase(k)}</span>
                ))}
                <span className="row" style={{ gap: 4 }}><span style={{ width: 10, height: 3, background: "var(--accent-2)" }} />candidate</span>
                <span className="row" style={{ gap: 4 }}><span style={{ width: 1, height: 10, background: "rgba(255,255,255,.4)" }} />scene cut</span>
              </div>
            </div>
          </div>
          <div className="card card-pad">
            <div className="strong" style={{ marginBottom: 10 }}>AI analysis</div>
            <div className="grid grid-2 small" style={{ gap: 8 }}>
              <div className="muted">Transcript</div><div>{v.transcript ? `${v.transcript.model} · ${v.transcript.device?.toUpperCase()} · ${v.transcript.elapsed_s}s` : v.transcript_status}</div>
              <div className="muted">Scenes</div><div>{(v.scene_cuts?.length ?? 0) + (v.scene_cuts ? 1 : 0)} shots</div>
              <div className="muted">Speech ratio</div><div>{v.analysis?.audio ? `${Math.round(v.analysis.audio.speech_ratio * 100)}%` : "—"}</div>
              <div className="muted">Spans considered</div><div>{v.analysis?.spans_considered ?? "—"}</div>
              <div className="muted">Semantic ranking</div><div>{v.analysis?.llm_model ?? "heuristic only"}</div>
              <div className="muted">Visual analysis</div><div>{v.analysis?.vision_backend ?? "—"}</div>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="card-header"><Sparkles size={15} /><span className="card-title">Candidates</span><span className="card-sub">{candidates.length}</span></div>
          <div className="col" style={{ gap: 0, maxHeight: 760, overflowY: "auto" }}>
            {candidates.length === 0 && <Empty icon={<Sparkles size={20} />} title="No candidates yet">They appear here after analysis.</Empty>}
            {candidates.map((c) => (
              <div key={c.id} className="row" style={{ padding: "12px 16px", borderBottom: "1px solid var(--border)", gap: 12, alignItems: "flex-start",
                opacity: c.status === "rejected" || c.duplicate_of ? 0.5 : 1 }}>
                <ScoreRing score={c.score} size={40} />
                <div className="grow">
                  <div className="strong small clamp-2">{c.title ?? c.text.slice(0, 80)}</div>
                  <div className="tiny faint mono" style={{ marginTop: 2 }}>{fmtTimecode(c.start)} → {fmtTimecode(c.end)} · {Math.round(c.duration)}s</div>
                  <div className="row-wrap" style={{ marginTop: 6, gap: 4 }}>
                    {c.labels?.hook_type && c.labels.hook_type !== "none" && <Badge color="violet">{titleCase(c.labels.hook_type)}</Badge>}
                    {c.status === "generated" && <StatusBadge status="ready" />}
                    {c.duplicate_of && <Badge color="yellow">duplicate</Badge>}
                  </div>
                </div>
                <div className="col" style={{ gap: 4 }}>
                  <Button size="sm" icon variant="ghost" title="Preview" onClick={() => seek(c.start)}><Play size={13} /></Button>
                  {c.status === "candidate" && <Button size="sm" icon title="Generate Short" onClick={() => genOne.mutate(c.id)}><Wand2 size={13} /></Button>}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <Modal open={showTranscript} onClose={() => setShowTranscript(false)} title="Transcript" wide>
        {transcript.data ? (
          <div className="col" style={{ gap: 2 }}>
            <div className="tiny faint" style={{ marginBottom: 8 }}>{transcript.data.model} · {transcript.data.language} · {transcript.data.device}</div>
            {(transcript.data.sentences as { start: number; text: string }[]).map((s, i) => (
              <div key={i} className="row small" style={{ alignItems: "flex-start", gap: 12, padding: "3px 0", cursor: "pointer" }}
                onClick={() => { setShowTranscript(false); seek(s.start); }}>
                <span className="mono faint" style={{ width: 70, flexShrink: 0 }}>{fmtDuration(s.start)}</span><span>{s.text}</span>
              </div>
            ))}
          </div>
        ) : <Skeleton h={200} />}
      </Modal>
    </div>
  );
}
