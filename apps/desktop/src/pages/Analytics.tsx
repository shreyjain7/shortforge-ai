import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BarChart3, Brain, RefreshCw, RotateCcw } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { Sparkline } from "../components/media";
import { Badge, Button, Empty, FieldRow, PageHeader, Stat, Toggle } from "../components/ui";
import { api } from "../lib/api";
import { useToast } from "../lib/events";
import { fmtCompact, fmtDateTime, fmtDuration } from "../lib/format";

export default function Analytics() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const { data } = useQuery({ queryKey: ["analytics"], queryFn: api.analytics });
  const { data: learning } = useQuery({ queryKey: ["learning"], queryFn: api.learning });
  const refresh = useMutation({ mutationFn: api.refreshAnalytics, onSuccess: () => toast({ title: "Fetching latest metrics from YouTube", level: "info" }),
    onError: (e: Error) => toast({ title: "Could not refresh", body: e.message, level: "error" }) });
  const run = useMutation({ mutationFn: api.runLearning, onSuccess: () => { toast({ title: "Learning update queued", level: "info" }); window.setTimeout(() => void qc.invalidateQueries({ queryKey: ["learning"] }), 3000); } });
  const reset = useMutation({ mutationFn: api.resetLearning, onSuccess: () => { void qc.invalidateQueries({ queryKey: ["learning"] }); toast({ title: "Ranking weights reset to defaults", level: "success" }); } });
  const toggle = useMutation({ mutationFn: (enabled: boolean) => api.patchSettings({ learning: { enabled } }), onSuccess: () => { void qc.invalidateQueries({ queryKey: ["learning"] }); void qc.invalidateQueries({ queryKey: ["settings"] }); } });
  const rows: any[] = data?.rows ?? [];
  const state = learning?.state ?? {};
  return (
    <div className="page">
      <PageHeader title="Analytics" subtitle="Real metrics from the YouTube APIs only — nothing is estimated or invented."
        actions={<Button onClick={() => refresh.mutate()} loading={refresh.isPending} disabled={!data?.connected}><RefreshCw size={14} /> Refresh metrics</Button>} />
      {!data?.connected && (
        <div className="card card-pad small" style={{ marginBottom: 14 }}>
          Connect your YouTube account in <a className="gradient-text strong" onClick={() => nav("/settings?tab=publishing")}>Settings → Publishing</a> to collect views, likes and comments for uploaded Shorts.
        </div>
      )}
      <div className="grid grid-4" style={{ marginBottom: 14 }}>
        <Stat label="Shorts with data" value={data?.totals.shorts ?? 0} />
        <Stat label="Total views" value={fmtCompact(data?.totals.views ?? 0)} accent />
        <Stat label="Likes" value={fmtCompact(data?.totals.likes ?? 0)} />
        <Stat label="Comments" value={fmtCompact(data?.totals.comments ?? 0)} />
      </div>
      <div className="grid" style={{ gridTemplateColumns: "minmax(0, 1.5fr) minmax(320px, 1fr)", alignItems: "start" }}>
        <div className="col" style={{ gap: 14 }}>
          <div className="card card-pad">
            <div className="row" style={{ marginBottom: 8 }}><span className="card-title">Views collected over time</span></div>
            {(data?.history?.length ?? 0) > 1 ? <Sparkline values={data!.history.map((h: any) => h.views)} height={90} /> : <div className="faint small">Needs at least two analytics refreshes.</div>}
          </div>
          <div className="card">
            <div className="card-header"><span className="card-title">Performance by Short</span></div>
            {rows.length === 0 ? <Empty icon={<BarChart3 size={22} />} title="No performance data yet">Metrics appear after Shorts are uploaded and analytics are refreshed.</Empty> : (
              <table className="table">
                <thead><tr><th>Short</th><th>AI score</th><th>Length</th><th>Views</th><th>Likes</th><th>Avg. view %</th><th>Published</th></tr></thead>
                <tbody>{rows.map((r) => (
                  <tr key={r.short.id} style={{ cursor: "pointer" }} onClick={() => nav(`/shorts/${r.short.id}`)}>
                    <td className="strong" style={{ maxWidth: 320 }}><div className="ellipsis">{r.short.title}</div></td>
                    <td className="mono">{r.short.score?.toFixed(0)}</td>
                    <td className="mono">{fmtDuration(r.short.duration)}</td>
                    <td className="mono">{fmtCompact(r.views)}</td>
                    <td className="mono">{fmtCompact(r.likes)}</td>
                    <td className="mono">{r.average_view_percentage != null ? `${r.average_view_percentage.toFixed(0)}%` : "—"}</td>
                    <td className="small muted">{fmtDateTime(r.published_at)}</td>
                  </tr>
                ))}</tbody>
              </table>
            )}
          </div>
        </div>
        <div className="col" style={{ gap: 14 }}>
          <div className="card">
            <div className="card-header"><Brain size={15} /><span className="card-title">Learning engine</span>
              <span className="right"><Toggle on={!!learning?.settings?.enabled} onChange={(v) => toggle.mutate(v)} /></span></div>
            <div className="card-pad">
              <div className="small muted">{state.reason ?? "Waiting for enough uploaded Shorts with mature metrics."}</div>
              {state.samples != null && <div className="tiny faint" style={{ marginTop: 4 }}>{state.samples} samples · minimum {learning?.settings?.min_samples} · updated {fmtDateTime(state.updated_at)}</div>}
              <div className="row" style={{ gap: 6, marginTop: 10 }}>
                <Button size="sm" onClick={() => run.mutate()}><RefreshCw size={13} /> Update now</Button>
                <Button size="sm" variant="ghost" onClick={() => confirm("Reset learned ranking weights to defaults?") && reset.mutate()}><RotateCcw size={13} /> Reset weights</Button>
              </div>
              <div className="divider" />
              <div className="small strong" style={{ marginBottom: 8 }}>Ranking weights</div>
              <div className="col" style={{ gap: 7 }}>
                {learning?.weights?.map((w: any) => (
                  <div key={w.metric}>
                    <div className="row tiny"><span className="muted">{w.label}</span>
                      {state.correlations?.[w.metric] != null && <Badge color={state.correlations[w.metric] > 0 ? "green" : "red"}>r={state.correlations[w.metric]}</Badge>}
                      <span className="right mono">{w.weight.toFixed(2)}</span></div>
                    <div className="bar thin" style={{ marginTop: 3, position: "relative" }}>
                      <span style={{ width: `${(w.weight / 2) * 100}%` }} />
                      <i style={{ position: "absolute", top: -2, bottom: -2, left: `${(w.default / 2) * 100}%`, width: 1, background: "rgba(255,255,255,.5)" }} />
                    </div>
                  </div>
                ))}
              </div>
              <div className="tiny faint" style={{ marginTop: 8 }}>White tick = default weight. Weights move slowly (bounded per update) and only after the minimum sample size.</div>
            </div>
          </div>
          <div className="card">
            <div className="card-header"><span className="card-title">Insights</span><span className="card-sub">correlational</span></div>
            <div className="card-pad col" style={{ gap: 10 }}>
              {(state.insights ?? []).length === 0 && <div className="faint small">No reliable patterns yet — insights need several Shorts per group.</div>}
              {(state.insights ?? []).map((i: any, k: number) => (
                <div key={k} className="small"><Badge color={i.effect > 0 ? "green" : "red"}>{i.effect > 0 ? "+" : ""}{i.effect} SD</Badge> <span className="muted">{i.text}</span></div>
              ))}
            </div>
          </div>
          <div className="card card-pad small">
            <FieldRow label="Minimum samples" desc="Before any weight changes">
              <input className="input" type="number" style={{ width: 90 }} defaultValue={learning?.settings?.min_samples}
                onBlur={(e) => api.patchSettings({ learning: { min_samples: Number(e.target.value) } }).then(() => qc.invalidateQueries({ queryKey: ["learning"] }))} />
            </FieldRow>
          </div>
        </div>
      </div>
    </div>
  );
}
