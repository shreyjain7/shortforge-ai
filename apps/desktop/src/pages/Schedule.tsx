import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, Clock, Trash2 } from "lucide-react";
import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { Badge, Button, Empty, PageHeader, Progress, StatusBadge } from "../components/ui";
import { api, mediaUrl } from "../lib/api";
import { useToast } from "../lib/events";
import { fmtDateTime } from "../lib/format";
import type { Upload } from "../lib/types";

function dayKey(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" });
}

export default function Schedule() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const { data: uploads } = useQuery({ queryKey: ["uploads", "scheduled"], queryFn: () => api.uploads("scheduled,uploading,failed"), refetchInterval: 8000 });
  const { data: slots } = useQuery({ queryKey: ["slots"], queryFn: () => api.schedulePreview(8) });
  const { data: settings } = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const { data: ready } = useQuery({ queryKey: ["shorts", "ready"], queryFn: () => api.shorts({ status: "ready", sort: "score", limit: 30 }) });
  const cancel = useMutation({ mutationFn: (id: number) => api.cancelUpload(id), onSuccess: () => { void qc.invalidateQueries({ queryKey: ["uploads"] }); toast({ title: "Upload cancelled", level: "info" }); } });
  const reschedule = useMutation({ mutationFn: ({ id, at }: { id: number; at: string }) => api.patchUpload(id, { scheduled_at: at }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["uploads"] }), onError: (e: Error) => toast({ title: "Could not reschedule", body: e.message, level: "error" }) });
  const schedule = useMutation({ mutationFn: (id: number) => api.scheduleShort(id), onSuccess: () => { void qc.invalidateQueries({ queryKey: ["uploads"] }); void qc.invalidateQueries({ queryKey: ["shorts"] }); toast({ title: "Scheduled at the next free slot", level: "success" }); },
    onError: (e: Error) => toast({ title: "Could not schedule", body: e.message, level: "error" }) });
  const groups = useMemo(() => {
    const g: Record<string, Upload[]> = {};
    for (const u of (uploads ?? []).slice().sort((a, b) => (a.publish_at ?? a.scheduled_at ?? "").localeCompare(b.publish_at ?? b.scheduled_at ?? ""))) {
      const k = dayKey(u.publish_at ?? u.scheduled_at ?? new Date().toISOString());
      (g[k] ??= []).push(u);
    }
    return g;
  }, [uploads]);
  const ap = settings?.autopilot;
  return (
    <div className="page">
      <PageHeader title="Schedule" subtitle={ap ? `${ap.schedule_strategy === "slots" ? `Daily slots ${ap.schedule_slots.join(" · ")}` : ap.schedule_strategy === "interval" ? `Every ≥ ${ap.min_upload_gap_min} min` : "Queue order"} · max ${ap.max_uploads_per_day}/day · ${ap.upload_mode === "publish_at" ? "uploaded early, YouTube publishes on time" : "uploaded at slot time"}` : ""}
        actions={<Button onClick={() => nav("/settings?tab=autopilot")}>Scheduling rules</Button>} />
      <div className="grid" style={{ gridTemplateColumns: "minmax(0, 1.6fr) minmax(300px, 1fr)", alignItems: "start" }}>
        <div className="col" style={{ gap: 14 }}>
          {Object.keys(groups).length === 0 && <div className="card"><Empty icon={<CalendarClock size={22} />} title="Nothing scheduled">Schedule a ready Short, or enable Auto Upload in Autopilot.</Empty></div>}
          {Object.entries(groups).map(([day, items]) => (
            <div key={day} className="card">
              <div className="card-header"><span className="card-title">{day}</span><span className="card-sub">{items.length} upload{items.length > 1 ? "s" : ""}</span></div>
              {items.map((u) => (
                <div key={u.id} className="row" style={{ padding: "12px 16px", borderBottom: "1px solid var(--border)", gap: 14 }}>
                  <div style={{ width: 44 }}>{u.short?.cover_url && <img src={mediaUrl(u.short.cover_url)} style={{ width: 44, height: 78, objectFit: "cover", borderRadius: 8 }} />}</div>
                  <div className="grow" style={{ cursor: "pointer" }} onClick={() => nav(`/shorts/${u.short_id}`)}>
                    <div className="strong">{u.title}</div>
                    <div className="row small muted" style={{ gap: 8, marginTop: 4 }}>
                      <Clock size={12} />{fmtDateTime(u.publish_at ?? u.scheduled_at)} <Badge>{u.visibility}</Badge> <StatusBadge status={u.status} />
                    </div>
                    {u.status === "uploading" && <div style={{ marginTop: 6, maxWidth: 300 }}><Progress value={u.progress} thin /></div>}
                    {u.error && <div className="tiny" style={{ color: "var(--danger)", marginTop: 4 }}>{u.error}</div>}
                  </div>
                  {u.status === "scheduled" && (
                    <input type="datetime-local" className="input" style={{ width: 200, height: 30 }} defaultValue={(u.publish_at ?? u.scheduled_at ?? "").slice(0, 16)}
                      onBlur={(e) => e.target.value && reschedule.mutate({ id: u.id, at: new Date(e.target.value).toISOString() })} />
                  )}
                  {(u.status === "scheduled" || u.status === "failed") && <Button size="sm" variant="ghost" icon title="Cancel" onClick={() => cancel.mutate(u.id)}><Trash2 size={14} /></Button>}
                </div>
              ))}
            </div>
          ))}
        </div>
        <div className="col" style={{ gap: 14 }}>
          <div className="card">
            <div className="card-header"><span className="card-title">Next free slots</span></div>
            <div className="card-pad col" style={{ gap: 6 }}>
              {slots?.next_slots.map((t) => <div key={t} className="row small"><Clock size={12} color="var(--text-3)" /> {fmtDateTime(t)}</div>)}
            </div>
          </div>
          <div className="card">
            <div className="card-header"><span className="card-title">Ready to schedule</span><span className="card-sub">{ready?.total ?? 0}</span></div>
            <div className="col">
              {ready?.items.length === 0 && <div className="card-pad faint small">No ready Shorts.</div>}
              {ready?.items.map((s) => (
                <div key={s.id} className="row" style={{ padding: "8px 14px", gap: 10, borderBottom: "1px solid var(--border)" }}>
                  <span className="small ellipsis grow" style={{ cursor: "pointer" }} onClick={() => nav(`/shorts/${s.id}`)}>{s.title}</span>
                  <Button size="sm" onClick={() => schedule.mutate(s.id)}>Schedule</Button>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
