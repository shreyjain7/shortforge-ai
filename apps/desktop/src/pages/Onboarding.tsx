import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowLeft, ArrowRight, Check, CheckCircle2, Cpu, Download, HardDrive, Link2, Loader2, Radio, Sparkles, XCircle } from "lucide-react";
import { type ReactNode, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Badge, Button, Progress, Segmented } from "../components/ui";
import { ToolsPanel } from "../components/ToolsPanel";
import { api } from "../lib/api";
import { useEvents, useToast } from "../lib/events";
import { fmtBytes } from "../lib/format";

const STEPS = ["Welcome", "Hardware", "Performance", "Storage", "Models", "YouTube", "First source"];

function Check_({ ok, label, detail }: { ok: boolean | undefined; label: string; detail?: ReactNode }) {
  return (
    <div className="row" style={{ padding: "10px 0", borderBottom: "1px solid var(--border)", gap: 10 }}>
      {ok === undefined ? <Loader2 size={16} className="spin" /> : ok ? <CheckCircle2 size={16} color="var(--success)" /> : <XCircle size={16} color="var(--danger)" />}
      <span className="strong">{label}</span>
      <span className="small muted right" style={{ textAlign: "right" }}>{detail}</span>
    </div>
  );
}

export default function Onboarding() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const { progress } = useEvents();
  const [step, setStep] = useState(0);
  const [profile, setProfile] = useState<{ ai: string; render: string } | null>(null);
  const [mode, setMode] = useState<"simple" | "advanced">("simple");
  const [dir, setDir] = useState("");
  const [source, setSource] = useState("");
  const { data: hw } = useQuery({ queryKey: ["hardware"], queryFn: () => api.hardware() });
  const { data: deps } = useQuery({ queryKey: ["deps"], queryFn: api.dependencies });
  const { data: models } = useQuery({ queryKey: ["models"], queryFn: api.models, refetchInterval: step === 4 ? 3000 : false });
  const { data: loc } = useQuery({ queryKey: ["storage-loc"], queryFn: api.storageLocation });
  const { data: jobs } = useQuery({ queryKey: ["jobs", "install"], queryFn: () => api.jobs({ type: "install_model", status: "queued,running" }), refetchInterval: step === 4 ? 2000 : false });
  const { data: acct } = useQuery({ queryKey: ["youtube-account"], queryFn: api.youtubeAccount, enabled: step === 5 });
  const install = useMutation({ mutationFn: (id: string) => api.installModel(id), onSuccess: () => void qc.invalidateQueries({ queryKey: ["jobs"] }) });
  const finish = useMutation({
    mutationFn: async () => {
      await api.completeOnboarding({ ai_profile: profile?.ai ?? hw?.recommended_ai_profile, render_profile: profile?.render ?? hw?.recommended_render_profile, mode });
      if (source.trim()) await api.addSource({ input: source.trim(), scan_now: true });
    },
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["settings"] }); toast({ title: "ShortForge is ready", body: source ? "Scanning your first source now." : undefined, level: "success" }); nav("/"); },
    onError: (e: Error) => toast({ title: "Could not finish setup", body: e.message, level: "error" }),
  });
  const rec = {
    whisper: `whisper:${hw?.recommended_whisper_model ?? "small"}`,
    llm: hw?.recommended_llm ? `llm:${hw.recommended_llm}` : null,
  };
  const wanted = [rec.whisper, "vision:yunet", rec.llm, "embedding:nomic-embed-text"].filter(Boolean) as string[];
  const installingIds = new Map((jobs?.items ?? []).map((j) => [String(j.payload.model_id), j]));
  const gpu = hw?.gpus?.[0];
  const next = () => setStep((s) => Math.min(STEPS.length - 1, s + 1));
  const back = () => setStep((s) => Math.max(0, s - 1));

  return (
    <div className="center" style={{ minHeight: "100vh", padding: 24, background: "radial-gradient(1000px 600px at 70% -10%, rgba(99,102,241,.14), transparent 60%), var(--bg)" }}>
      <div style={{ width: "min(760px, 100%)" }}>
        <div className="row" style={{ marginBottom: 18, gap: 6 }}>
          {STEPS.map((s, i) => (
            <div key={s} className="grow" title={s}>
              <div style={{ height: 3, borderRadius: 3, background: i <= step ? "var(--grad)" : "var(--surface-3)", transition: "background .3s" }} />
              <div className="tiny" style={{ marginTop: 6, color: i === step ? "var(--text)" : "var(--text-3)" }}>{s}</div>
            </div>
          ))}
        </div>
        <div className="card" style={{ padding: 28, minHeight: 420, position: "relative", overflow: "hidden" }}>
          <AnimatePresence mode="wait">
            <motion.div key={step} initial={{ opacity: 0, x: 16 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -16 }} transition={{ duration: 0.22 }}>
              {step === 0 && (
                <div className="col" style={{ gap: 16, alignItems: "flex-start" }}>
                  <div className="brand-logo" style={{ width: 56, height: 56, borderRadius: 18 }}><Sparkles size={26} color="#fff" /></div>
                  <h1 style={{ margin: 0, fontSize: 30, letterSpacing: "-0.03em" }}>Welcome to <span className="gradient-text">ShortForge AI</span></h1>
                  <p className="muted" style={{ fontSize: 15, maxWidth: 560, margin: 0 }}>
                    Turn any public YouTube channel into polished vertical Shorts — transcribed, understood, reframed, captioned, mastered and scheduled —
                    entirely on this computer. No subscriptions, no cloud AI.
                  </p>
                  <div className="grid grid-3" style={{ width: "100%", marginTop: 8 }}>
                    {[["Private", "Your media and AI never leave this PC"], ["Free", "Local open models, no paid APIs"], ["Autonomous", "Set it up once, let Autopilot run"]].map(([a, b]) => (
                      <div key={a} className="card card-pad"><div className="strong">{a}</div><div className="small muted">{b}</div></div>
                    ))}
                  </div>
                  <div className="row small muted" style={{ gap: 10 }}>Experience:
                    <Segmented value={mode} onChange={setMode} options={[{ value: "simple", label: "Simple" }, { value: "advanced", label: "Advanced" }]} />
                  </div>
                </div>
              )}
              {step === 1 && (
                <div>
                  <h2 style={{ marginTop: 0 }}><Cpu size={20} /> Your hardware</h2>
                  <Check_ ok={hw ? !!gpu : undefined} label="GPU" detail={gpu ? `${gpu.name} · ${(gpu.vram_total_mb / 1024).toFixed(0)} GB VRAM` : "No NVIDIA GPU — CPU mode"} />
                  <Check_ ok={hw ? true : undefined} label="CPU & memory" detail={hw && `${hw.cpu_name} · ${hw.cpu_threads} threads · ${hw.ram_total_gb} GB RAM`} />
                  <Check_ ok={deps?.ffmpeg.ok} label="FFmpeg" detail={deps?.ffmpeg.ok ? `${deps.ffmpeg.version}${deps.ffmpeg.libass ? " · libass" : ""}` : <>Install with <span className="kbd">winget install Gyan.FFmpeg</span></>} />
                  <Check_ ok={hw ? hw.cuda_available : undefined} label="CUDA" detail={hw?.cuda_available ? "GPU-accelerated transcription" : "CPU transcription (slower)"} />
                  <Check_ ok={hw ? Object.values(hw.nvenc ?? {}).some(Boolean) : undefined} label="NVENC" detail={hw && Object.entries(hw.nvenc ?? {}).filter(([, v]) => v).map(([k]) => k.split("_")[0].toUpperCase()).join(" · ")} />
                  <Check_ ok={deps?.ollama.running} label="Ollama (local LLM)" detail={deps?.ollama.running ? `${deps.ollama.models.length} models installed` : deps?.ollama.installed ? "installed — will be started automatically" : <>Optional · <a href="https://ollama.com" target="_blank" rel="noreferrer" className="gradient-text">ollama.com</a></>} />
                  <Check_ ok={deps ? deps.js_runtime.length > 0 : undefined} label="JavaScript runtime" detail={deps?.js_runtime.join(", ") || "Install Node.js for reliable YouTube downloads"} />
                  {hw?.notes?.map((n: string) => <div key={n} className="tiny faint" style={{ marginTop: 8 }}>{n}</div>)}
                  {deps && (!deps.ffmpeg.ok || deps.js_runtime.length === 0) && (
                    <div className="card card-pad" style={{ marginTop: 14 }}>
                      <div className="strong small" style={{ marginBottom: 8 }}>Install missing tools</div>
                      <ToolsPanel compact />
                    </div>
                  )}
                </div>
              )}
              {step === 2 && hw && (
                <div>
                  <h2 style={{ marginTop: 0 }}>Performance profile</h2>
                  <p className="muted">Recommended for your machine: <b style={{ color: "var(--text)" }}>AI {hw.recommended_ai_profile}</b> · <b style={{ color: "var(--text)" }}>Render {hw.recommended_render_profile}</b></p>
                  <div className="col" style={{ gap: 14, marginTop: 14 }}>
                    <div><div className="label">AI profile</div>
                      <Segmented value={profile?.ai ?? hw.recommended_ai_profile} onChange={(v) => setProfile({ ai: v, render: profile?.render ?? hw.recommended_render_profile })}
                        options={[{ value: "LOW", label: "Low" }, { value: "BALANCED", label: "Balanced" }, { value: "QUALITY", label: "Quality" }]} /></div>
                    <div><div className="label">Render profile</div>
                      <Segmented value={profile?.render ?? hw.recommended_render_profile} onChange={(v) => setProfile({ ai: profile?.ai ?? hw.recommended_ai_profile, render: v })}
                        options={[{ value: "FAST", label: "Fast" }, { value: "BALANCED", label: "Balanced" }, { value: "ULTRA", label: "Ultra" }]} /></div>
                    <div className="small muted">Transcription model: <b>{hw.recommended_whisper_model}</b> ({hw.recommended_compute_type}) · LLM: <b>{hw.recommended_llm ?? "none"}</b>. Models load one at a time to fit {gpu ? `${(gpu.vram_total_mb / 1024).toFixed(0)} GB` : "your"} VRAM.</div>
                  </div>
                </div>
              )}
              {step === 3 && (
                <div>
                  <h2 style={{ marginTop: 0 }}><HardDrive size={20} /> Storage</h2>
                  <p className="muted">Sources, proxies, renders, models and cache live here. A fast SSD with 50+ GB free is ideal.</p>
                  <div className="card card-pad small mono">{loc?.current}</div>
                  <div className="row" style={{ marginTop: 12, gap: 8 }}>
                    <input className="input" placeholder="Optional: another folder, e.g. D:\ShortForgeData" value={dir} onChange={(e) => setDir(e.target.value)} />
                    <Button disabled={!dir} onClick={() => api.setStorageLocation(dir).then((r) => toast({ title: r.restart_required ? "Saved — takes effect after restart" : "Saved", level: "info" })).catch((e: Error) => toast({ title: "Cannot use that folder", body: e.message, level: "error" }))}>Use folder</Button>
                  </div>
                  {hw && <div className="tiny faint" style={{ marginTop: 10 }}>{hw.storage_free_gb} GB free on this drive.</div>}
                </div>
              )}
              {step === 4 && (
                <div>
                  <h2 style={{ marginTop: 0 }}><Download size={20} /> Local AI models</h2>
                  <p className="muted">Recommended for your hardware. Nothing downloads until you click Install — sizes are shown first.</p>
                  <div className="col" style={{ gap: 10 }}>
                    {models?.items.filter((m) => wanted.includes(m.id)).map((m) => {
                      const job = installingIds.get(m.id);
                      return (
                        <div key={m.id} className="card card-pad">
                          <div className="row">
                            <div className="grow"><div className="strong">{m.name}</div><div className="small muted">{m.purpose}</div></div>
                            <span className="small faint">{fmtBytes(m.size_mb * 1024 * 1024)}</span>
                            {m.installed ? <Badge color="green"><Check size={11} /> Installed</Badge> : job ? <Badge color="blue" dot>Downloading</Badge> :
                              <Button size="sm" variant="primary" disabled={!m.available} onClick={() => install.mutate(m.id)}>Install</Button>}
                          </div>
                          {job && <div style={{ marginTop: 10 }}><Progress value={progress[job.id]?.progress ?? job.progress} /></div>}
                          {!m.available && <div className="tiny faint" style={{ marginTop: 6 }}>Requires Ollama to be installed and running.</div>}
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
              {step === 5 && (
                <div>
                  <h2 style={{ marginTop: 0 }}><Link2 size={20} /> Connect YouTube (optional)</h2>
                  <p className="muted">Needed only to upload and schedule automatically. You can do this later in Settings → Publishing.</p>
                  {acct?.connected ? <Badge color="green"><CheckCircle2 size={11} /> Connected as {acct.channel?.title}</Badge> : (
                    <div className="card card-pad small">Not connected yet — open <b>Settings → Publishing</b> after setup to connect in two clicks.</div>
                  )}
                  <div className="small faint" style={{ marginTop: 14 }}>You'll create a free Google OAuth "Desktop app" client; tokens are kept in Windows Credential Manager.</div>
                </div>
              )}
              {step === 6 && (
                <div>
                  <h2 style={{ marginTop: 0 }}><Radio size={20} /> Add your first source</h2>
                  <p className="muted">Any public YouTube channel, @handle, playlist or video — or a local file path.</p>
                  <input className="input input-lg" placeholder="@SomeCreator or https://www.youtube.com/watch?v=…" value={source} onChange={(e) => setSource(e.target.value)} autoFocus />
                  <div className="small faint" style={{ marginTop: 10 }}>ShortForge will scan it, download recent videos, find the strongest moments and render polished Shorts.</div>
                </div>
              )}
            </motion.div>
          </AnimatePresence>
        </div>
        <div className="row" style={{ marginTop: 16 }}>
          {step > 0 && <Button variant="ghost" onClick={back}><ArrowLeft size={14} /> Back</Button>}
          <div className="right row" style={{ gap: 8 }}>
            {step < STEPS.length - 1 ? <Button variant="primary" size="lg" onClick={next}>Continue <ArrowRight size={15} /></Button> : (
              <Button variant="primary" size="lg" loading={finish.isPending} onClick={() => finish.mutate()}>{source.trim() ? "Start processing" : "Finish setup"} <ArrowRight size={15} /></Button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
