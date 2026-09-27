import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { Heart, Play, Scissors, Sparkles, ThumbsDown, Wand2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ScoreBreakdown } from "../components/media";
import { Badge, Button, Empty, Modal, PageHeader, ScoreRing, Skeleton, Segmented, stagger } from "../components/ui";
import { api, mediaUrl } from "../lib/api";
import { useToast } from "../lib/events";
import { fmtTimecode, scoreColor, titleCase } from "../lib/format";
import type { Candidate } from "../lib/types";

function ClipPreview({ c, onClose }: { c: Candidate; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const nav = useNavigate();
  const ref = useRef<HTMLVideoElement>(null);
  const { data: full } = useQuery({ queryKey: ["candidate", c.id], queryFn: () => api.candidate(c.id) });
  const [bounds, setBounds] = useState({ start: c.start, end: c.end });
  useEffect(() => setBounds({ start: c.start, end: c.end }), [c]);
  const save = useMutation({
    mutationFn: () => api.editCandidate(c.id, bounds.start, bounds.end),
    onSuccess: (r) => { setBounds({ start: r.start, end: r.end }); toast({ title: "Boundaries updated", body: "Snapped to the nearest gaps between words.", level: "success" });
      void qc.invalidateQueries({ queryKey: ["candidates"] }); void qc.invalidateQueries({ queryKey: ["candidate", c.id] }); },
    onError: (e: Error) => toast({ title: "Could not update", body: e.message, level: "error" }),
  });
  const gen = useMutation({ mutationFn: () => api.generateCandidate(c.id), onSuccess: (r) => { onClose(); nav(`/shorts/${r.short_id}`); } });
  const playRange = () => { if (ref.current) { ref.current.currentTime = bounds.start; void ref.current.play(); } };
  const nudge = (k: "start" | "end", d: number) => setBounds((b) => ({ ...b, [k]: Math.max(0, +(b[k] + d).toFixed(2)) }));
  const cand = full ?? c;
  return (
    <Modal open onClose={onClose} wide title={cand.title ?? "Candidate clip"}
      footer={<><Button variant="ghost" onClick={onClose}>Close</Button>
        <Button variant="primary" loading={gen.isPending} onClick={() => gen.mutate()}><Wand2 size={14} /> Generate Short</Button></>}>
      <div className="grid" style={{ gridTemplateColumns: "minmax(0, 1.25fr) minmax(0, 1fr)", gap: 20 }}>
        <div>
          {cand.video?.proxy_url ? (
            <video ref={ref} src={mediaUrl(cand.video.proxy_url)} controls style={{ width: "100%", borderRadius: 12, background: "#000" }}
              onLoadedMetadata={(e) => { e.currentTarget.currentTime = bounds.start; }}
              onTimeUpdate={(e) => { if (e.currentTarget.currentTime > bounds.end) e.currentTarget.pause(); }} />
          ) : <div className="thumb" />}
          <div className="row" style={{ marginTop: 10 }}>
            <Button size="sm" onClick={playRange}><Play size={13} /> Play clip</Button>
            <span className="small mono faint">{fmtTimecode(bounds.start)} → {fmtTimecode(bounds.end)} · {(bounds.end - bounds.start).toFixed(1)}s</span>
          </div>
          <div className="card card-pad" style={{ marginTop: 12 }}>
            <div className="row small strong" style={{ marginBottom: 8 }}><Scissors size={14} /> Edit boundaries</div>
            {(["start", "end"] as const).map((k) => (
              <div key={k} className="row" style={{ gap: 6, marginBottom: 6 }}>
                <span className="small muted" style={{ width: 40 }}>{titleCase(k)}</span>
                <Button size="sm" onClick={() => nudge(k, -1)}>−1s</Button>
                <Button size="sm" onClick={() => nudge(k, -0.25)}>−¼</Button>
                <input className="input mono" style={{ width: 100, height: 28 }} value={bounds[k]}
                  onChange={(e) => setBounds({ ...bounds, [k]: Number(e.target.value) || 0 })} />
                <Button size="sm" onClick={() => nudge(k, 0.25)}>+¼</Button>
                <Button size="sm" onClick={() => nudge(k, 1)}>+1s</Button>
                <Button size="sm" variant="ghost" onClick={() => ref.current && setBounds({ ...bounds, [k]: +ref.current.currentTime.toFixed(2) })}>Set to playhead</Button>
              </div>
            ))}
            <Button size="sm" variant="primary" loading={save.isPending} onClick={() => save.mutate()}>Apply (snaps to words)</Button>
          </div>
          <div className="small" style={{ marginTop: 12, lineHeight: 1.6, color: "var(--text-2)" }}>{cand.text}</div>
        </div>
        <div>
          <div className="row" style={{ gap: 14, marginBottom: 14 }}>
            <ScoreRing score={cand.score} size={64} stroke={5} />
            <div>
              <div className="strong">Overall {cand.score.toFixed(1)}</div>
              <div className="tiny faint">heuristic {cand.heuristic_score.toFixed(1)}{cand.llm_score != null && ` · LLM ${cand.llm_score.toFixed(1)}`} · pass {cand.pass_reached}/8</div>
              <div className="tiny faint">A ranking heuristic — not a virality prediction.</div>
            </div>
          </div>
          {cand.scores ? <ScoreBreakdown rows={cand.scores} /> : <Skeleton h={220} />}
          {cand.reasoning && <div className="small muted" style={{ marginTop: 12 }}><span className="strong" style={{ color: "var(--text)" }}>Why: </span>{cand.reasoning}</div>}
          {cand.hook_text && <div className="small" style={{ marginTop: 8 }}><Badge color="cyan">On-screen hook</Badge> {cand.hook_text}</div>}
          {cand.keywords.length > 0 && <div className="row-wrap" style={{ marginTop: 10, gap: 4 }}>{cand.keywords.map((k) => <Badge key={k}>{k}</Badge>)}</div>}
          {cand.vision?.hook && (
            <div className="small" style={{ marginTop: 12 }}>
              <div className="strong">Hook optimizer</div>
              <div className="row" style={{ gap: 12, marginTop: 4 }}>
                {(["window_1s", "window_3s", "window_5s"] as const).map((k) => (
                  <span key={k} className="faint">{k.replace("window_", "")}: <b style={{ color: scoreColor(cand.vision.hook[k]) }}>{Math.round(cand.vision.hook[k])}</b></span>
                ))}
              </div>
            </div>
          )}
          {cand.notes.length > 0 && <div className="tiny faint" style={{ marginTop: 10 }}>{cand.notes.join(" · ")}</div>}
        </div>
      </div>
    </Modal>
  );
}

export default function Candidates() {
  const qc = useQueryClient();
  const toast = useToast();
  const [sort, setSort] = useState<"score" | "newest" | "duration">("score");
  const [filter, setFilter] = useState<"all" | "favorite" | "generated">("all");
  const [minScore, setMinScore] = useState(0);
  const [preview, setPreview] = useState<Candidate | null>(null);
  const { data, isLoading } = useQuery({
    queryKey: ["candidates", sort, filter, minScore],
    queryFn: () => api.candidates({ sort, favorite: filter === "favorite" || undefined, status: filter === "generated" ? "generated" : undefined,
      min_score: minScore || undefined, limit: 120 }),
  });
  const refresh = () => void qc.invalidateQueries({ queryKey: ["candidates"] });
  const gen = useMutation({ mutationFn: (id: number) => api.generateCandidate(id), onSuccess: () => { toast({ title: "Short queued", level: "success" }); refresh(); } });
  const rej = useMutation({ mutationFn: (id: number) => api.rejectCandidate(id), onSuccess: refresh });
  const fav = useMutation({ mutationFn: (id: number) => api.favoriteCandidate(id), onSuccess: refresh });
  return (
    <div className="page">
      <PageHeader title="Candidates" subtitle="Every moment the clip finder ranked, with its full score breakdown." />
      <div className="toolbar">
        <Segmented value={filter} onChange={setFilter} options={[{ value: "all", label: "All" }, { value: "favorite", label: "Favorites" }, { value: "generated", label: "Generated" }]} />
        <Segmented value={sort} onChange={setSort} options={[{ value: "score", label: "Top score" }, { value: "newest", label: "Newest" }, { value: "duration", label: "Shortest" }]} />
        <div className="row small right" style={{ gap: 8 }}>
          <span className="faint">Min score</span>
          <input type="range" className="range" min={0} max={90} step={5} value={minScore} style={{ width: 140 }} onChange={(e) => setMinScore(Number(e.target.value))} />
          <span className="mono" style={{ width: 24 }}>{minScore}</span>
        </div>
      </div>
      {isLoading && <div className="grid grid-3">{[0, 1, 2].map((i) => <Skeleton key={i} h={260} />)}</div>}
      {data?.items.length === 0 && <div className="card"><Empty icon={<Sparkles size={22} />} title="No candidates">Analyze a video to find its strongest moments.</Empty></div>}
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))" }}>
        {data?.items.map((c, i) => (
          <motion.div key={c.id} className="card" {...stagger(i)} whileHover={{ y: -3 }} style={{ overflow: "hidden", opacity: c.duplicate_of ? 0.55 : 1 }}>
            <div className="thumb" style={{ borderRadius: 0, cursor: "pointer" }} onClick={() => setPreview(c)}>
              <img src={mediaUrl(`/api/media/candidate/${c.id}/frame`)} loading="lazy" onError={(e) => { if (c.video?.thumbnail_url) e.currentTarget.src = mediaUrl(c.video.thumbnail_url)!; }} />
              <span className="duration">{Math.round(c.duration)}s</span>
              <div style={{ position: "absolute", top: 8, right: 8 }}><ScoreRing score={c.score} size={42} /></div>
              {c.favorite && <Heart size={16} fill="#f472b6" color="#f472b6" style={{ position: "absolute", top: 10, left: 10 }} />}
            </div>
            <div className="card-pad">
              <div className="strong clamp-2" style={{ minHeight: 38 }}>{c.title ?? c.text.slice(0, 90)}</div>
              <div className="tiny faint mono" style={{ marginTop: 4 }}>{fmtTimecode(c.start)} → {fmtTimecode(c.end)}</div>
              <div className="tiny faint ellipsis">{c.video?.title}</div>
              <div className="grid grid-4" style={{ gap: 6, marginTop: 10 }}>
                {(["hook", "standalone", "payoff", "visual_activity"] as const).map((m) => (
                  <div key={m} style={{ textAlign: "center", background: "var(--surface)", borderRadius: 8, padding: "5px 0", border: "1px solid var(--border)" }}>
                    <div className="tiny faint">{{ hook: "Hook", standalone: "Context", payoff: "Payoff", visual_activity: "Visual" }[m]}</div>
                    <div className="strong" style={{ color: scoreColor(c.metrics?.[m]) }}>{c.metrics?.[m] != null ? Math.round(c.metrics[m]) : "—"}</div>
                  </div>
                ))}
              </div>
              <div className="row" style={{ marginTop: 12, gap: 6 }}>
                <Button size="sm" onClick={() => setPreview(c)}><Play size={13} /> Preview</Button>
                {c.status === "candidate" ? <Button size="sm" variant="primary" onClick={() => gen.mutate(c.id)}><Wand2 size={13} /> Generate</Button>
                  : <Badge color={c.status === "generated" ? "green" : c.status === "rejected" ? "red" : ""}>{titleCase(c.status)}</Badge>}
                <Button size="sm" variant="ghost" icon className="right" title="Favorite" onClick={() => fav.mutate(c.id)}>
                  <Heart size={14} fill={c.favorite ? "#f472b6" : "none"} color={c.favorite ? "#f472b6" : undefined} /></Button>
                <Button size="sm" variant="ghost" icon title={c.status === "rejected" ? "Restore" : "Reject"} onClick={() => rej.mutate(c.id)}><ThumbsDown size={14} /></Button>
              </div>
            </div>
          </motion.div>
        ))}
      </div>
      {preview && <ClipPreview c={preview} onClose={() => setPreview(null)} />}
    </div>
  );
}
