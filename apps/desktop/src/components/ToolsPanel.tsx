import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Download, XCircle } from "lucide-react";
import { API, api } from "../lib/api";
import { useEvents, useToast } from "../lib/events";
import { Button, Progress } from "./ui";

interface Tool { name: string; label: string; size_mb: number; available: boolean; managed: boolean; detail: string | null }

/** FFmpeg / Deno status with one-click managed installs (no admin rights, no winget needed). */
export function ToolsPanel({ compact }: { compact?: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const { progress } = useEvents();
  const { data: tools } = useQuery({
    queryKey: ["tools"],
    queryFn: async () => (await fetch(`${API}/system/tools`)).json() as Promise<Tool[]>,
    refetchInterval: 4000,
  });
  const { data: jobs } = useQuery({ queryKey: ["jobs", "tools"], queryFn: () => api.jobs({ type: "install_tool", status: "queued,running" }), refetchInterval: 2000 });
  const install = useMutation({
    mutationFn: (name: string) => fetch(`${API}/system/tools/${name}/install`, { method: "POST", headers: { "X-ShortForge-Client": "desktop" } }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["jobs"] }); toast({ title: "Download started", level: "info" }); },
  });
  const running = new Map((jobs?.items ?? []).map((j) => [String(j.payload.tool), j]));
  return (
    <div className="col" style={{ gap: compact ? 6 : 10 }}>
      {tools?.map((t) => {
        const job = running.get(t.name);
        const p = job ? progress[job.id] : undefined;
        return (
          <div key={t.name} className="row" style={{ gap: 10, alignItems: "flex-start" }}>
            {t.available ? <CheckCircle2 size={16} color="var(--success)" /> : <XCircle size={16} color="var(--danger)" />}
            <div className="grow">
              <div className="strong small">{t.label}</div>
              <div className="tiny faint ellipsis">{t.available ? (t.managed ? "Installed by ShortForge" : `Found: ${t.detail ?? "system"}`) : `Required · about ${t.size_mb} MB`}</div>
              {job && <div style={{ marginTop: 6 }}><Progress value={p?.progress ?? job.progress} thin /><div className="tiny faint" style={{ marginTop: 3 }}>{p?.message ?? job.message}</div></div>}
            </div>
            {!t.available && !job && <Button size="sm" variant="primary" onClick={() => install.mutate(t.name)}><Download size={13} /> Install</Button>}
          </div>
        );
      })}
    </div>
  );
}
