import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { AudioLines, Boxes, Brain, CheckCircle2, Download, Eye, Layers, Trash2, Zap } from "lucide-react";
import { useState } from "react";
import { Badge, Button, Modal, PageHeader, Progress, stagger } from "../components/ui";
import { api } from "../lib/api";
import { useEvents, useToast } from "../lib/events";
import { fmtBytes } from "../lib/format";
import type { ModelItem } from "../lib/types";

const KIND = {
  whisper: { label: "Transcription", icon: <AudioLines size={15} /> },
  llm: { label: "Language models", icon: <Brain size={15} /> },
  vision: { label: "Vision", icon: <Eye size={15} /> },
  embedding: { label: "Embeddings", icon: <Layers size={15} /> },
};

export default function Models() {
  const qc = useQueryClient();
  const toast = useToast();
  const { progress } = useEvents();
  const { data } = useQuery({ queryKey: ["models"], queryFn: api.models, refetchInterval: 10000 });
  const { data: jobs } = useQuery({ queryKey: ["jobs", "install"], queryFn: () => api.jobs({ type: "install_model", status: "queued,running" }), refetchInterval: 3000 });
  const [confirmInstall, setConfirmInstall] = useState<ModelItem | null>(null);
  const install = useMutation({ mutationFn: (id: string) => api.installModel(id), onSuccess: () => { toast({ title: "Download started", level: "info" }); setConfirmInstall(null); void qc.invalidateQueries({ queryKey: ["jobs"] }); } });
  const remove = useMutation({ mutationFn: (id: string) => api.removeModel(id), onSuccess: () => void qc.invalidateQueries({ queryKey: ["models"] }),
    onError: (e: Error) => toast({ title: "Could not remove", body: e.message, level: "error" }) });
  const activate = useMutation({ mutationFn: (id: string) => api.activateModel(id), onSuccess: () => { void qc.invalidateQueries({ queryKey: ["models"] }); toast({ title: "Model activated", level: "success" }); } });
  const installing = new Map((jobs?.items ?? []).map((j) => [String(j.payload.model_id), j]));
  const active = data?.active ?? {};
  const isActive = (m: ModelItem) => (m.kind === "whisper" && (active.whisper === m.id.split(":")[1] || (active.whisper === "auto" && data?.recommended.whisper === m.id.split(":")[1])))
    || (m.kind === "llm" && (active.llm === m.source || (active.llm === "auto" && data?.recommended.llm === m.source)));
  return (
    <div className="page">
      <PageHeader title="Models" subtitle={`Local models only. Sizes are shown before anything downloads. ${data?.vram_mb ? `GPU: ${(data.vram_mb / 1024).toFixed(0)} GB VRAM` : ""}`} />
      {(["whisper", "llm", "vision", "embedding"] as const).map((kind) => (
        <div key={kind} style={{ marginBottom: 22 }}>
          <div className="row" style={{ marginBottom: 10 }}>{KIND[kind].icon}<span className="strong">{KIND[kind].label}</span></div>
          <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(290px, 1fr))" }}>
            {data?.items.filter((m) => m.kind === kind).map((m, i) => {
              const job = installing.get(m.id);
              const p = job ? progress[job.id]?.progress ?? job.progress : null;
              const tooBig = data.vram_mb > 0 && m.vram_mb > data.vram_mb;
              return (
                <motion.div key={m.id} className="card card-pad" {...stagger(i)} style={{ opacity: m.available ? 1 : 0.6 }}>
                  <div className="row">
                    <span className="strong">{m.name}</span>
                    {isActive(m) && m.installed && <Badge color="green"><CheckCircle2 size={11} /> Active</Badge>}
                    {m.recommended_for.includes(data.recommended.profile) && !m.installed && <Badge color="violet"><Zap size={11} /> Recommended</Badge>}
                  </div>
                  <div className="small muted" style={{ marginTop: 4, minHeight: 36 }}>{m.purpose}</div>
                  <div className="row-wrap tiny faint" style={{ marginTop: 8, gap: 10 }}>
                    <span>{fmtBytes(m.size_mb * 1024 * 1024)} download</span><span>~{(m.vram_mb / 1024).toFixed(1)} GB VRAM</span>
                    {tooBig && <span style={{ color: "var(--warning)" }}>exceeds your VRAM</span>}
                  </div>
                  <div className="tiny faint ellipsis" style={{ marginTop: 4 }} title={m.location}>{m.installed ? m.location : m.source}</div>
                  {job && <div style={{ marginTop: 10 }}><Progress value={p ?? 0} /><div className="tiny faint" style={{ marginTop: 4 }}>{progress[job.id]?.message ?? job.message}</div></div>}
                  <div className="row" style={{ marginTop: 12, gap: 6 }}>
                    {!m.installed && !job && <Button size="sm" variant="primary" disabled={!m.available} onClick={() => setConfirmInstall(m)}><Download size={13} /> Install</Button>}
                    {m.installed && (m.kind === "whisper" || m.kind === "llm") && !isActive(m) && <Button size="sm" onClick={() => activate.mutate(m.id)}>Activate</Button>}
                    {m.installed && (m.kind === "llm" || m.kind === "embedding") && !job && <Button size="sm" variant="ghost" title="Pull the latest version from Ollama" onClick={() => install.mutate(m.id)}>Update</Button>}
                    {m.installed && <Button size="sm" variant="ghost" icon className="right" title="Remove" onClick={() => confirm(`Remove ${m.name}?`) && remove.mutate(m.id)}><Trash2 size={13} /></Button>}
                    {!m.available && <span className="tiny faint">Requires Ollama running</span>}
                  </div>
                </motion.div>
              );
            })}
          </div>
        </div>
      ))}
      <Modal open={!!confirmInstall} onClose={() => setConfirmInstall(null)} title={`Install ${confirmInstall?.name}?`}
        footer={<><Button variant="ghost" onClick={() => setConfirmInstall(null)}>Cancel</Button>
          <Button variant="primary" loading={install.isPending} onClick={() => confirmInstall && install.mutate(confirmInstall.id)}><Download size={14} /> Download {confirmInstall && fmtBytes(confirmInstall.size_mb * 1024 * 1024)}</Button></>}>
        <div className="small muted">
          This downloads about <b style={{ color: "var(--text)" }}>{confirmInstall && fmtBytes(confirmInstall.size_mb * 1024 * 1024)}</b> from{" "}
          {confirmInstall?.kind === "whisper" ? "Hugging Face" : confirmInstall?.kind === "vision" ? "the OpenCV model zoo" : "the Ollama library"} and stores it locally.
          After installation it runs fully offline.
        </div>
      </Modal>
      {!data && <Boxes />}
    </div>
  );
}
