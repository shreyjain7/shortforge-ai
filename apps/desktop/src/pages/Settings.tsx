import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { CheckCircle2, ExternalLink, KeyRound, Link2, Plug, RefreshCw, XCircle } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Badge, Button, FieldRow, PageHeader, Segmented, Toggle } from "../components/ui";
import { ToolsPanel } from "../components/ToolsPanel";
import { CheckForUpdatesButton, ReleaseNotes, UpdateProgress, useUpdater } from "../components/UpdateBanner";
import { api } from "../lib/api";
import { appVersion } from "../lib/native";
import { useToast } from "../lib/events";
import { fmtBytes, fmtDateTime } from "../lib/format";
import type { Settings as S } from "../lib/types";

type Tab = { id: string; label: string; advanced?: boolean };
const TABS: Tab[] = [
  { id: "general", label: "General" }, { id: "autopilot", label: "Autopilot" }, { id: "publishing", label: "Publishing" },
  { id: "youtube", label: "YouTube sources" }, { id: "ai", label: "AI models", advanced: true }, { id: "clips", label: "Clip finding", advanced: true },
  { id: "editing", label: "Editing & captions", advanced: true }, { id: "render", label: "Render & audio", advanced: true },
  { id: "storage", label: "Storage" }, { id: "notifications", label: "Notifications" }, { id: "system", label: "GPU & queue", advanced: true },
  { id: "debug", label: "Debug tools" },
  { id: "about", label: "About & updates" },
];

function useSettings() {
  const qc = useQueryClient();
  const toast = useToast();
  const { data } = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const m = useMutation({
    mutationFn: (patch: S) => api.patchSettings(patch),
    onSuccess: (d) => qc.setQueryData(["settings"], d),
    onError: (e: Error) => toast({ title: "Could not save setting", body: e.message, level: "error" }),
  });
  const set = (section: string, key: string, value: unknown) => m.mutate({ [section]: { [key]: value } });
  return { s: data, set, saving: m.isPending };
}

function Num({ value, onCommit, min, max, step = 1, width = 100, suffix }: { value: number | null; onCommit: (v: number | null) => void; min?: number; max?: number; step?: number; width?: number; suffix?: string }) {
  const [v, setV] = useState(value ?? "");
  useEffect(() => setV(value ?? ""), [value]);
  return (
    <div className="row" style={{ gap: 6 }}>
      <input className="input" type="number" style={{ width }} value={v} min={min} max={max} step={step}
        onChange={(e) => setV(e.target.value)} onBlur={() => onCommit(v === "" ? null : Number(v))} />
      {suffix && <span className="small faint">{suffix}</span>}
    </div>
  );
}

function Sel<T extends string>({ value, options, onChange, width = 200 }: { value: T; options: (T | { v: T; l: string })[]; onChange: (v: T) => void; width?: number }) {
  return (
    <select className="select" style={{ width }} value={value} onChange={(e) => onChange(e.target.value as T)}>
      {options.map((o) => typeof o === "string" ? <option key={o} value={o}>{o}</option> : <option key={o.v} value={o.v}>{o.l}</option>)}
    </select>
  );
}

function Section({ title, children, desc }: { title: string; children: ReactNode; desc?: string }) {
  return (
    <div className="card card-pad" style={{ marginBottom: 14 }}>
      <div className="strong" style={{ fontSize: 14 }}>{title}</div>
      {desc && <div className="small faint" style={{ marginTop: 2 }}>{desc}</div>}
      <div style={{ marginTop: 6 }}>{children}</div>
    </div>
  );
}

function PublishingTab() {
  const qc = useQueryClient();
  const toast = useToast();
  const { data: acct, refetch } = useQuery({ queryKey: ["youtube-account"], queryFn: api.youtubeAccount, refetchInterval: (q) => (q.state.data?.auth_status === "waiting" ? 2000 : false) });
  const [json, setJson] = useState("");
  const saveClient = useMutation({ mutationFn: () => api.youtubeClient(json), onSuccess: () => { setJson(""); void refetch(); toast({ title: "OAuth client saved securely", level: "success" }); },
    onError: (e: Error) => toast({ title: "Invalid client file", body: e.message, level: "error" }) });
  const connect = useMutation({ mutationFn: api.youtubeConnect, onSuccess: () => { toast({ title: "Complete sign-in in your browser", level: "info" }); void refetch(); },
    onError: (e: Error) => toast({ title: "Could not start sign-in", body: e.message, level: "error" }) });
  const disconnect = useMutation({ mutationFn: api.youtubeDisconnect, onSuccess: () => { void qc.invalidateQueries({ queryKey: ["youtube-account"] }); } });
  return (
    <>
      <Section title="YouTube account" desc="Uploads use the official YouTube Data API with OAuth. Tokens are stored in Windows Credential Manager — never in files or logs.">
        {acct?.connected ? (
          <div className="row" style={{ gap: 12, padding: "10px 0" }}>
            {acct.channel?.avatar && <img src={acct.channel.avatar} style={{ width: 40, height: 40, borderRadius: 40 }} />}
            <div className="grow"><div className="strong">{acct.channel?.title ?? "Connected"}</div><div className="tiny faint">{acct.channel?.id}</div></div>
            <Badge color="green"><CheckCircle2 size={11} /> Connected</Badge>
            <Button size="sm" variant="danger" onClick={() => disconnect.mutate()}>Disconnect</Button>
          </div>
        ) : (
          <div className="col" style={{ gap: 10, paddingTop: 6 }}>
            <div className="small muted">
              1. In <a href="https://console.cloud.google.com/apis/credentials" target="_blank" rel="noreferrer" className="gradient-text strong">Google Cloud Console <ExternalLink size={11} /></a>, enable
              the <b>YouTube Data API v3</b> (and optionally <b>YouTube Analytics API</b>), create an OAuth client of type <b>Desktop app</b> and download its JSON.
              2. Paste it below. 3. Connect.
            </div>
            {acct?.client_configured ? <Badge color="green"><KeyRound size={11} /> OAuth client configured</Badge> : (
              <>
                <textarea className="textarea mono" rows={4} placeholder='{"installed": {"client_id": "...", "client_secret": "...", ...}}' value={json} onChange={(e) => setJson(e.target.value)} />
                <div><Button size="sm" disabled={!json.trim()} loading={saveClient.isPending} onClick={() => saveClient.mutate()}><KeyRound size={13} /> Save client securely</Button></div>
              </>
            )}
            <div className="row" style={{ gap: 8 }}>
              <Button variant="primary" disabled={!acct?.client_configured} loading={connect.isPending || acct?.auth_status === "waiting"} onClick={() => connect.mutate()}><Link2 size={14} /> Connect YouTube account</Button>
              {acct?.auth_message && <span className={clsx("small", acct.auth_status === "error" ? "" : "muted")} style={{ color: acct.auth_status === "error" ? "var(--danger)" : undefined }}>{acct.auth_message}</span>}
            </div>
          </div>
        )}
      </Section>
    </>
  );
}

function WeightsSection() {
  const qc = useQueryClient();
  const toast = useToast();
  const { data } = useQuery({ queryKey: ["learning"], queryFn: api.learning });
  const [w, setW] = useState<Record<string, number>>({});
  useEffect(() => { if (data) setW(Object.fromEntries(data.weights.map((x: any) => [x.metric, x.weight]))); }, [data]);
  const save = useMutation({ mutationFn: () => api.setWeights(w), onSuccess: () => { void qc.invalidateQueries({ queryKey: ["learning"] }); toast({ title: "Ranking weights saved", level: "success" }); } });
  return (
    <Section title="Ranking weights" desc="How much each metric contributes to the final clip score. The learning engine may nudge these gradually.">
      <div className="grid grid-2" style={{ gap: "4px 24px" }}>
        {data?.weights.map((x: any) => (
          <div key={x.metric}>
            <div className="row small"><span className="muted">{x.label}</span><span className="right mono">{(w[x.metric] ?? x.weight).toFixed(2)}</span></div>
            <input type="range" className="range" min={0} max={3} step={0.05} value={w[x.metric] ?? x.weight} onChange={(e) => setW({ ...w, [x.metric]: Number(e.target.value) })} />
          </div>
        ))}
      </div>
      <div className="row" style={{ marginTop: 10, gap: 8 }}>
        <Button size="sm" variant="primary" loading={save.isPending} onClick={() => save.mutate()}>Save weights</Button>
        <Button size="sm" variant="ghost" onClick={() => api.resetLearning().then(() => qc.invalidateQueries({ queryKey: ["learning"] }))}>Reset to defaults</Button>
      </div>
    </Section>
  );
}

function AboutTab() {
  const u = useUpdater();
  const { data: health } = useQuery({ queryKey: ["health"], queryFn: api.health });
  const { data: storage } = useQuery({ queryKey: ["storage-loc"], queryFn: api.storageLocation });
  const [app, setApp] = useState<string | null>(null);
  useEffect(() => { void appVersion().then(setApp); }, []);
  return (
    <>
      <Section title="ShortForge AI">
        <div className="grid grid-2 small" style={{ gap: 8, marginTop: 6 }}>
          <span className="muted">App version</span><span className="mono">{app ?? "browser"}</span>
          <span className="muted">Engine version</span><span className="mono">{health?.version}</span>
          <span className="muted">Data folder</span><span className="mono ellipsis" title={storage?.current}>{storage?.current}</span>
          <span className="muted">Source code</span><a className="gradient-text strong" href="https://github.com/shreyjain7/shortforge-ai" target="_blank" rel="noreferrer">github.com/shreyjain7/shortforge-ai</a>
        </div>
      </Section>
      <Section title="Updates" desc="ShortForge checks GitHub Releases for signed updates every few hours. Updates install in the background and keep your projects and settings.">
        <div className="row" style={{ gap: 10, marginTop: 8 }}>
          <CheckForUpdatesButton />
          {u.phase === "uptodate" && <Badge color="green">You're up to date</Badge>}
          {u.phase === "error" && <span className="small" style={{ color: "var(--danger)" }}>{u.error}</span>}
          {u.phase === "available" && u.update && <Button size="sm" variant="primary" onClick={() => void u.install()}>Install {u.update.version}</Button>}
        </div>
        {(u.phase === "downloading" || u.phase === "installing") && <div style={{ marginTop: 12 }}><UpdateProgress /></div>}
        {u.update?.notes && (u.phase === "available" || u.phase === "downloading") && (
          <div className="card card-pad" style={{ marginTop: 12 }}><div className="strong small" style={{ marginBottom: 6 }}>What's new in {u.update.version}</div><ReleaseNotes notes={u.update.notes} /></div>
        )}
      </Section>
    </>
  );
}

function DebugTab() {
  const [level, setLevel] = useState<string>("");
  const { data: logs, refetch } = useQuery({ queryKey: ["logs", level], queryFn: () => api.logs(400, level || undefined) });
  const { data: deps } = useQuery({ queryKey: ["deps"], queryFn: api.dependencies });
  const { data: hw, refetch: rehw } = useQuery({ queryKey: ["hardware"], queryFn: () => api.hardware() });
  const ok = (b: boolean) => (b ? <CheckCircle2 size={14} color="var(--success)" /> : <XCircle size={14} color="var(--danger)" />);
  return (
    <>
      <Section title="External tools" desc="Installed automatically into the ShortForge data folder when missing.">
        <ToolsPanel />
      </Section>
      <Section title="Dependencies">
        {deps && (
          <div className="grid grid-2 small" style={{ gap: 8 }}>
            <div className="row">{ok(deps.ffmpeg.ok)} FFmpeg {deps.ffmpeg.version} {deps.ffmpeg.libass ? "· libass" : ""}</div>
            <div className="row">{ok(deps.cuda.ok)} CUDA ({deps.cuda.devices} device)</div>
            <div className="row">{ok(Object.values(deps.nvenc ?? {}).some(Boolean))} NVENC {Object.entries(deps.nvenc ?? {}).filter(([, v]) => v).map(([k]) => k.replace("_nvenc", "")).join(", ")}</div>
            <div className="row">{ok(deps.ollama.running)} Ollama {deps.ollama.running ? `· ${deps.ollama.models.length} models` : deps.ollama.installed ? "installed, not running" : "not installed"}</div>
            <div className="row">{ok(true)} yt-dlp {deps.yt_dlp.version}</div>
            <div className="row">{ok(deps.js_runtime.length > 0)} JS runtime for YouTube: {deps.js_runtime.join(", ") || "none"}</div>
            <div className="row">{ok(deps.keyring !== "file-fallback")} Secret storage: {deps.keyring}</div>
            <div className="row">{ok(true)} Data API key: {deps.youtube_api_key ? "configured" : "not set (optional)"}</div>
          </div>
        )}
      </Section>
      <Section title="Hardware">
        <div className="row small" style={{ marginBottom: 8 }}><span className="muted">Detected at start-up</span><Button size="sm" variant="ghost" className="right" onClick={() => api.hardware(true).then(() => rehw())}><RefreshCw size={13} /> Re-detect</Button></div>
        <pre className="log" style={{ maxHeight: 260 }}>{JSON.stringify(hw, null, 2)}</pre>
      </Section>
      <Section title="Logs" desc="Structured, rotating logs. Tokens, passwords and API keys are redacted before writing.">
        <div className="row" style={{ marginBottom: 8, gap: 6 }}>
          <Segmented value={level} onChange={setLevel} options={[{ value: "", label: "All" }, { value: "INFO", label: "Info" }, { value: "WARNING", label: "Warnings" }, { value: "ERROR", label: "Errors" }]} />
          <Button size="sm" className="right" onClick={() => refetch()}><RefreshCw size={13} /> Refresh</Button>
        </div>
        <div className="log">{logs?.slice().reverse().map((l) => `${l.ts?.slice(11, 19)} ${String(l.level).padEnd(7)} ${l.logger}: ${l.msg}${l.exc ? "\n" + l.exc : ""}`).join("\n")}</div>
      </Section>
    </>
  );
}

export default function Settings() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "general";
  const { s, set } = useSettings();
  const qc = useQueryClient();
  const toast = useToast();
  const { data: presets } = useQuery({ queryKey: ["presets"], queryFn: api.captionPresets });
  const { data: secretsInfo, refetch: refetchSecrets } = useQuery({ queryKey: ["secrets"], queryFn: api.secrets });
  const { data: storageLoc } = useQuery({ queryKey: ["storage-loc"], queryFn: api.storageLocation, enabled: tab === "storage" });
  const { data: storage } = useQuery({ queryKey: ["storage"], queryFn: () => api.storage(true), enabled: tab === "storage" });
  const { data: notifications } = useQuery({ queryKey: ["notifications"], queryFn: api.notifications, enabled: tab === "notifications" });
  const [apiKey, setApiKey] = useState("");
  const [newDir, setNewDir] = useState("");
  if (!s) return <div className="page" />;
  const advanced = s.general.mode === "advanced";
  const tabs = TABS.filter((t) => advanced || !t.advanced);
  const tg = (section: string, key: string) => <Toggle on={!!s[section][key]} onChange={(v) => set(section, key, v)} />;
  return (
    <div className="page" style={{ maxWidth: 1200 }}>
      <PageHeader title="Settings" actions={
        <Segmented value={s.general.mode} onChange={(v) => set("general", "mode", v)} options={[{ value: "simple", label: "Simple mode" }, { value: "advanced", label: "Advanced mode" }]} />} />
      <div className="grid" style={{ gridTemplateColumns: "200px minmax(0, 1fr)", gap: 20, alignItems: "start" }}>
        <div className="col" style={{ gap: 2, position: "sticky", top: 0 }}>
          {tabs.map((t) => (
            <button key={t.id} className={clsx("nav-item", tab === t.id && "active")} style={{ border: "none", background: tab === t.id ? undefined : "transparent", textAlign: "left", cursor: "pointer" }}
              onClick={() => setParams({ tab: t.id })}>{t.label}</button>
          ))}
        </div>
        <div>
          {tab === "general" && (
            <>
              <Section title="Performance profiles" desc="Chosen from your hardware; override any time.">
                <FieldRow label="AI profile" desc="Model sizes and precision"><Sel value={s.general.ai_profile} options={["LOW", "BALANCED", "QUALITY"]} onChange={(v) => set("general", "ai_profile", v)} /></FieldRow>
                <FieldRow label="Render profile" desc="Encoder preset/quality"><Sel value={s.general.render_profile} options={["FAST", "BALANCED", "ULTRA"]} onChange={(v) => set("general", "render_profile", v)} /></FieldRow>
                <FieldRow label="Automatically generate Shorts" desc="Render the best candidates as soon as a video is analysed">{tg("general", "auto_generate_shorts")}</FieldRow>
              </Section>
              <Section title="Defaults">
                <FieldRow label="Caption style"><Sel value={s.captions.preset} options={presets?.map((p) => p.name) ?? [s.captions.preset]} onChange={(v) => set("captions", "preset", v)} /></FieldRow>
                <FieldRow label="Short length" desc="Candidate duration window (seconds)">
                  <div className="row"><Num value={s.clips.min_duration} onCommit={(v) => set("clips", "min_duration", v ?? 15)} width={70} /><span className="faint">to</span><Num value={s.clips.max_duration} onCommit={(v) => set("clips", "max_duration", v ?? 60)} width={70} /></div>
                </FieldRow>
                <FieldRow label="Shorts per video"><Num value={s.clips.max_shorts_per_video} onCommit={(v) => set("clips", "max_shorts_per_video", v ?? 3)} width={70} /></FieldRow>
                <FieldRow label="Reframing mode"><Sel value={s.reframe.mode} options={["auto", "speaker", "conversation", "podcast", "presentation", "tech", "gameplay", "product", "cinematic"]} onChange={(v) => set("reframe", "mode", v)} /></FieldRow>
              </Section>
            </>
          )}
          {tab === "autopilot" && (
            <>
              <Section title="Autopilot" desc="Monitor sources, process new uploads, render, quality-check and (optionally) upload — unattended.">
                <FieldRow label="Autopilot"><Toggle on={s.autopilot.enabled} onChange={(v) => set("autopilot", "enabled", v)} /></FieldRow>
                <FieldRow label="Scan interval" desc="How often channels are checked for new uploads"><Num value={s.autopilot.scan_interval_min} suffix="min" onCommit={(v) => set("autopilot", "scan_interval_min", v ?? 60)} /></FieldRow>
                <FieldRow label="Minimum clip score" desc="Only candidates at or above this become Shorts"><Num value={s.autopilot.min_clip_score} onCommit={(v) => set("autopilot", "min_clip_score", v ?? 75)} /></FieldRow>
                <FieldRow label="Maximum clips per video"><Num value={s.autopilot.max_clips_per_video} onCommit={(v) => set("autopilot", "max_clips_per_video", v ?? 3)} /></FieldRow>
                <FieldRow label="Caption preset"><Sel value={s.autopilot.caption_preset} options={presets?.map((p) => p.name) ?? []} onChange={(v) => set("autopilot", "caption_preset", v)} /></FieldRow>
                <FieldRow label="Render preset"><Sel value={s.autopilot.render_profile} options={["FAST", "BALANCED", "ULTRA"]} onChange={(v) => set("autopilot", "render_profile", v)} /></FieldRow>
                <FieldRow label="Require QC PASS for upload" desc="Shorts with warnings wait in Review for approval">{tg("autopilot", "require_qc_pass")}</FieldRow>
              </Section>
              <Section title="Uploading & schedule">
                <FieldRow label="Auto upload" desc="Upload ready Shorts to the connected YouTube account">{tg("autopilot", "auto_upload")}</FieldRow>
                <FieldRow label="Maximum uploads per day"><Num value={s.autopilot.max_uploads_per_day} onCommit={(v) => set("autopilot", "max_uploads_per_day", v ?? 4)} /></FieldRow>
                <FieldRow label="Minimum gap between uploads"><Num value={s.autopilot.min_upload_gap_min} suffix="min" onCommit={(v) => set("autopilot", "min_upload_gap_min", v ?? 90)} /></FieldRow>
                <FieldRow label="Strategy"><Sel value={s.autopilot.schedule_strategy} options={[{ v: "slots", l: "Daily time slots" }, { v: "interval", l: "Minimum interval" }, { v: "queue", l: "Queue order (ASAP)" }]} onChange={(v) => set("autopilot", "schedule_strategy", v)} /></FieldRow>
                <FieldRow label="Daily slots" desc="Local time, comma-separated">
                  <input className="input" style={{ width: 240 }} defaultValue={s.autopilot.schedule_slots.join(", ")}
                    onBlur={(e) => set("autopilot", "schedule_slots", e.target.value.split(",").map((x) => x.trim()).filter((x) => /^\d{1,2}:\d{2}$/.test(x)))} />
                </FieldRow>
                <FieldRow label="Publishing method" desc="Upload early with a scheduled publish time (works while the PC is off), or upload at slot time">
                  <Sel value={s.autopilot.upload_mode} options={[{ v: "publish_at", l: "Scheduled publish (recommended)" }, { v: "at_slot", l: "Upload at slot time" }]} onChange={(v) => set("autopilot", "upload_mode", v)} width={260} />
                </FieldRow>
                <FieldRow label="Visibility"><Sel value={s.autopilot.default_visibility} options={["public", "unlisted", "private"]} onChange={(v) => set("autopilot", "default_visibility", v)} /></FieldRow>
                <FieldRow label="Made for kids">{tg("autopilot", "made_for_kids")}</FieldRow>
                <FieldRow label="Add to playlist" desc="Playlist ID (optional)">
                  <input className="input" style={{ width: 240 }} defaultValue={s.autopilot.playlist_id ?? ""} onBlur={(e) => set("autopilot", "playlist_id", e.target.value.trim() || null)} />
                </FieldRow>
              </Section>
            </>
          )}
          {tab === "publishing" && <PublishingTab />}
          {tab === "youtube" && (
            <>
              <Section title="Acquisition">
                <FieldRow label="Download quality" desc="Never fetches above the selected height"><Sel value={s.youtube.download_quality} options={[{ v: "1080", l: "1080p" }, { v: "1440", l: "1440p" }, { v: "2160", l: "2160p (4K)" }, { v: "best", l: "Best available" }]} onChange={(v) => set("youtube", "download_quality", v)} /></FieldRow>
                <FieldRow label="JavaScript runtime" desc="Used by yt-dlp to read YouTube's player"><Sel value={s.youtube.js_runtime} options={["node", "deno", "bun", "auto"]} onChange={(v) => set("youtube", "js_runtime", v)} /></FieldRow>
                <FieldRow label="Cookies from browser" desc="Only if YouTube asks to confirm you're not a bot (e.g. 'chrome', 'edge', 'firefox')">
                  <input className="input" style={{ width: 160 }} defaultValue={s.youtube.cookies_from_browser ?? ""} onBlur={(e) => set("youtube", "cookies_from_browser", e.target.value.trim() || null)} />
                </FieldRow>
                <FieldRow label="Download speed limit"><Num value={s.youtube.rate_limit_kbps} suffix="KB/s (blank = unlimited)" onCommit={(v) => set("youtube", "rate_limit_kbps", v)} /></FieldRow>
              </Section>
              <Section title="YouTube Data API (optional)" desc="A free API key gives exact publish dates and statistics during discovery. Everything works without it.">
                <FieldRow label="API key" desc={secretsInfo?.youtube_api_key ? "Stored in Windows Credential Manager" : "Not configured"}>
                  <div className="row"><input className="input" type="password" style={{ width: 220 }} placeholder={secretsInfo?.youtube_api_key ? "••••••••" : "AIza…"} value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
                    <Button size="sm" onClick={() => api.setSecret("youtube_api_key", apiKey || null).then(() => { setApiKey(""); void refetchSecrets(); toast({ title: apiKey ? "API key saved" : "API key removed", level: "success" }); })}>{apiKey ? "Save" : "Clear"}</Button></div>
                </FieldRow>
                <FieldRow label="Use the Data API when available">{tg("youtube", "use_data_api")}</FieldRow>
              </Section>
            </>
          )}
          {tab === "ai" && (
            <>
              <Section title="Transcription (faster-whisper)">
                <FieldRow label="Model"><Sel value={s.transcription.model} options={["auto", "tiny", "base", "small", "medium", "large-v3-turbo", "distil-large-v3", "large-v3"]} onChange={(v) => set("transcription", "model", v)} /></FieldRow>
                <FieldRow label="Device"><Sel value={s.transcription.device} options={["auto", "cuda", "cpu"]} onChange={(v) => set("transcription", "device", v)} /></FieldRow>
                <FieldRow label="Precision"><Sel value={s.transcription.compute_type} options={["auto", "float16", "int8_float16", "int8"]} onChange={(v) => set("transcription", "compute_type", v)} /></FieldRow>
                <FieldRow label="Language" desc="Blank = auto-detect"><input className="input" style={{ width: 100 }} defaultValue={s.transcription.language ?? ""} onBlur={(e) => set("transcription", "language", e.target.value.trim() || null)} /></FieldRow>
                <FieldRow label="Batch size" desc="Lower if you hit GPU memory limits"><Num value={s.transcription.batch_size} onCommit={(v) => set("transcription", "batch_size", v ?? 8)} /></FieldRow>
                <FieldRow label="Beam size"><Num value={s.transcription.beam_size} onCommit={(v) => set("transcription", "beam_size", v ?? 5)} /></FieldRow>
              </Section>
              <Section title="Local LLM">
                <FieldRow label="Provider"><Sel value={s.llm.provider} options={[{ v: "ollama", l: "Ollama" }, { v: "llamacpp", l: "llama.cpp server" }, { v: "none", l: "None (heuristics only)" }]} onChange={(v) => set("llm", "provider", v)} /></FieldRow>
                <FieldRow label="Model" desc="'auto' picks the best installed chat model"><input className="input" style={{ width: 200 }} defaultValue={s.llm.model} onBlur={(e) => set("llm", "model", e.target.value.trim() || "auto")} /></FieldRow>
                <FieldRow label="Ollama URL"><input className="input" style={{ width: 240 }} defaultValue={s.llm.ollama_url} onBlur={(e) => set("llm", "ollama_url", e.target.value.trim())} /></FieldRow>
                <FieldRow label="llama.cpp URL"><input className="input" style={{ width: 240 }} defaultValue={s.llm.llamacpp_url} onBlur={(e) => set("llm", "llamacpp_url", e.target.value.trim())} /></FieldRow>
                <FieldRow label="Temperature"><Num value={s.llm.temperature} step={0.05} onCommit={(v) => set("llm", "temperature", v ?? 0.2)} /></FieldRow>
                <FieldRow label="Candidates ranked by LLM" desc="PASS 4 budget per video"><Num value={s.llm.max_candidates} onCommit={(v) => set("llm", "max_candidates", v ?? 16)} /></FieldRow>
                <FieldRow label="Unload after use" desc="Frees VRAM between stages (recommended for 8 GB GPUs)">{tg("llm", "unload_after_use")}</FieldRow>
              </Section>
              <Section title="Prompt templates" desc="Override the system prompts used by the local LLM. Leave empty for the built-in prompts. Outputs are still schema-validated and grounded against the transcript.">
                {(["clip_ranker", "timeline", "metadata"] as const).map((k) => (
                  <div key={k} style={{ marginTop: 10 }}>
                    <label className="label">{{ clip_ranker: "Clip ranking (PASS 4)", timeline: "Semantic timeline", metadata: "Titles & descriptions" }[k]}</label>
                    <textarea className="textarea mono" rows={3} defaultValue={s.prompts?.[k] ?? ""} placeholder="(built-in prompt)" onBlur={(e) => set("prompts", k, e.target.value)} />
                  </div>
                ))}
              </Section>
            </>
          )}
          {tab === "clips" && (
            <>
            <WeightsSection />
            <Section title="Candidate generation">
              <FieldRow label="Target duration"><Num value={s.clips.target_duration} suffix="s" onCommit={(v) => set("clips", "target_duration", v ?? 35)} /></FieldRow>
              <FieldRow label="Minimum score (manual mode)"><Num value={s.clips.min_score} onCommit={(v) => set("clips", "min_score", v ?? 65)} /></FieldRow>
              <FieldRow label="Duplicate threshold" desc="0–1; higher allows more similar clips"><Num value={s.clips.duplicate_threshold} step={0.05} onCommit={(v) => set("clips", "duplicate_threshold", v ?? 0.8)} /></FieldRow>
              <FieldRow label="Silence trimming">{tg("clips", "silence_trim")}</FieldRow>
              <FieldRow label="Pacing" desc="How aggressively pauses between words are shortened">
                <Segmented value={s.clips.pacing} onChange={(v) => set("clips", "pacing", v)} options={[{ value: "natural", label: "Natural" }, { value: "balanced", label: "Balanced" }, { value: "aggressive", label: "Aggressive" }]} />
              </FieldRow>
            </Section>
            </>
          )}
          {tab === "editing" && (
            <>
              <Section title="Reframing">
                <FieldRow label="Face sampling rate"><Num value={s.reframe.sample_fps} suffix="fps" onCommit={(v) => set("reframe", "sample_fps", v ?? 6)} /></FieldRow>
                <FieldRow label="Minimum hold before re-framing"><Num value={s.reframe.min_hold_s} step={0.1} suffix="s" onCommit={(v) => set("reframe", "min_hold_s", v ?? 1.2)} /></FieldRow>
                <FieldRow label="Dead-zone" desc="Fraction of the frame the subject may move without a camera move"><Num value={s.reframe.deadzone} step={0.01} onCommit={(v) => set("reframe", "deadzone", v ?? 0.06)} /></FieldRow>
                <FieldRow label="Automatic punch-ins">{tg("reframe", "punch_ins")}</FieldRow>
                <FieldRow label="Maximum punch-in"><Num value={s.reframe.max_punch_in} step={0.01} onCommit={(v) => set("reframe", "max_punch_in", v ?? 1.12)} /></FieldRow>
              </Section>
              <Section title="Captions">
                <FieldRow label="Captions enabled">{tg("captions", "enabled")}</FieldRow>
                <FieldRow label="Semantic emphasis">{tg("captions", "emphasis")}</FieldRow>
              </Section>
              <Section title="Safe area" desc="Portions of the frame covered by platform UI. Captions and hooks never enter these regions.">
                {(["top", "bottom", "left", "right"] as const).map((k) => (
                  <FieldRow key={k} label={k[0].toUpperCase() + k.slice(1)}><Num value={s.safe_area[k]} step={0.01} onCommit={(v) => set("safe_area", k, v ?? 0.1)} /></FieldRow>
                ))}
              </Section>
              <Section title="B-roll library" desc="Local clips are tagged automatically (file names + a local vision model when installed) and matched to what is being said.">
                <FieldRow label="Use B-roll">{tg("broll", "enabled")}</FieldRow>
                <FieldRow label="Library folder"><input className="input" style={{ width: 280 }} placeholder="D:\\Media\\B-roll" defaultValue={s.broll.library_dir ?? ""} onBlur={(e) => set("broll", "library_dir", e.target.value.trim() || null)} /></FieldRow>
                <FieldRow label="Mode" desc="Suggest = offered in the editor; Automatic = inserted automatically">
                  <Segmented value={s.broll.mode} onChange={(v) => set("broll", "mode", v)} options={[{ value: "suggest", label: "Suggest" }, { value: "auto", label: "Automatic" }]} />
                </FieldRow>
                <FieldRow label="Maximum inserts per Short"><Num value={s.broll.max_inserts} onCommit={(v) => set("broll", "max_inserts", v ?? 2)} /></FieldRow>
                <FieldRow label="Insert length"><Num value={s.broll.insert_duration} step={0.5} suffix="s" onCommit={(v) => set("broll", "insert_duration", v ?? 2)} /></FieldRow>
              </Section>
              <Section title="Music" desc="Optional background music from your own library: analysed for tempo, beats and energy, ducked under speech.">
                <FieldRow label="Add music">{tg("music", "enabled")}</FieldRow>
                <FieldRow label="Music folder"><input className="input" style={{ width: 280 }} placeholder="D:\\Media\\Music" defaultValue={s.music.library_dir ?? ""} onBlur={(e) => set("music", "library_dir", e.target.value.trim() || null)} /></FieldRow>
                <FieldRow label="Music level"><Num value={s.music.volume_db} suffix="dB" onCommit={(v) => set("music", "volume_db", v ?? -22)} /></FieldRow>
                <FieldRow label="Ducking under speech"><Num value={s.music.duck_db} suffix="dB" onCommit={(v) => set("music", "duck_db", v ?? -10)} /></FieldRow>
              </Section>
              <Section title="Visual enhancements" desc="All optional, applied at render time.">
                {(["sharpen", "contrast", "color", "vignette", "denoise"] as const).map((k) => <FieldRow key={k} label={k[0].toUpperCase() + k.slice(1)}>{tg("enhance", k)}</FieldRow>)}
              </Section>
            </>
          )}
          {tab === "render" && (
            <>
              <Section title="Video encoding">
                <FieldRow label="Resolution"><Sel value={`${s.render.width}x${s.render.height}`} options={[{ v: "1080x1920", l: "1080 × 1920" }, { v: "1440x2560", l: "1440 × 2560" }]}
                  onChange={(v) => { const [w, h] = v.split("x").map(Number); api.patchSettings({ render: { width: w, height: h } }).then(() => qc.invalidateQueries({ queryKey: ["settings"] })); }} /></FieldRow>
                <FieldRow label="Codec"><Sel value={s.render.codec} options={[{ v: "h264", l: "H.264" }, { v: "hevc", l: "HEVC" }, { v: "av1", l: "AV1" }]} onChange={(v) => set("render", "codec", v)} /></FieldRow>
                <FieldRow label="Encoder"><Sel value={s.render.encoder} options={[{ v: "auto", l: "Auto (NVENC if available)" }, { v: "nvenc", l: "NVIDIA NVENC" }, { v: "cpu", l: "CPU (x264/x265/SVT-AV1)" }]} onChange={(v) => set("render", "encoder", v)} width={240} /></FieldRow>
                <FieldRow label="Frame rate"><Sel value={s.render.fps} options={[{ v: "auto", l: "Match source" }, { v: "30", l: "30 fps" }, { v: "60", l: "60 fps" }]} onChange={(v) => set("render", "fps", v)} /></FieldRow>
                <FieldRow label="Constant quality (CQ/CRF)" desc="Blank = profile default"><Num value={s.render.cq} onCommit={(v) => set("render", "cq", v)} /></FieldRow>
                <FieldRow label="Bitrate cap"><Num value={s.render.bitrate_kbps} suffix="kbps" onCommit={(v) => set("render", "bitrate_kbps", v)} /></FieldRow>
                <FieldRow label="Audio bitrate"><Num value={s.render.audio_bitrate_kbps} suffix="kbps" onCommit={(v) => set("render", "audio_bitrate_kbps", v ?? 192)} /></FieldRow>
              </Section>
              <Section title="Audio mastering">
                <FieldRow label="Loudness target"><Num value={s.audio.target_lufs} step={0.5} suffix="LUFS" onCommit={(v) => set("audio", "target_lufs", v ?? -14)} /></FieldRow>
                <FieldRow label="True-peak ceiling"><Num value={s.audio.true_peak} step={0.1} suffix="dBTP" onCommit={(v) => set("audio", "true_peak", v ?? -1.5)} /></FieldRow>
                <FieldRow label="Compressor">{tg("audio", "compressor")}</FieldRow>
                <FieldRow label="Voice EQ">{tg("audio", "eq")}</FieldRow>
                <FieldRow label="Noise reduction">{tg("audio", "denoise")}</FieldRow>
              </Section>
            </>
          )}
          {tab === "storage" && (
            <>
              <Section title="Data location">
                <div className="small">Current: <span className="mono">{storageLoc?.current}</span></div>
                <div className="row" style={{ marginTop: 10, gap: 8 }}>
                  <input className="input" placeholder="D:\ShortForgeData" value={newDir} onChange={(e) => setNewDir(e.target.value)} />
                  <Button disabled={!newDir} onClick={() => api.setStorageLocation(newDir).then((r) => toast({ title: r.restart_required ? "Restart ShortForge to use the new location" : "Saved", level: "info" })).catch((e: Error) => toast({ title: "Invalid location", body: e.message, level: "error" }))}>Change</Button>
                </div>
              </Section>
              <Section title="Usage">
                {storage && Object.entries(storage.folders).map(([k, v]) => <div key={k} className="row small" style={{ padding: "4px 0" }}><span className="muted">{k}</span><span className="right mono">{fmtBytes(v)}</span></div>)}
                {storage && <div className="tiny faint" style={{ marginTop: 6 }}>{fmtBytes(storage.disk_free)} free of {fmtBytes(storage.disk_total)}</div>}
              </Section>
              <Section title="Cleanup policy" desc="Final renders are never deleted automatically.">
                <FieldRow label="Automatic cleanup">{tg("storage", "auto_cleanup")}</FieldRow>
                <FieldRow label="Maximum cache size"><Num value={s.storage.max_cache_gb} suffix="GB" onCommit={(v) => set("storage", "max_cache_gb", v ?? 50)} /></FieldRow>
                <FieldRow label="Delete temp files older than"><Num value={s.storage.temp_max_age_h} suffix="hours" onCommit={(v) => set("storage", "temp_max_age_h", v ?? 24)} /></FieldRow>
                <FieldRow label="Keep source media" desc="Turn off to delete downloaded sources after their Shorts are rendered">{tg("storage", "keep_source_media")}</FieldRow>
                <FieldRow label="Keep proxy media">{tg("storage", "keep_proxy_media")}</FieldRow>
                <FieldRow label="Minimum free space" desc="Downloads and renders pause below this"><Num value={s.min_free_disk_gb} suffix="GB" onCommit={(v) => api.patchSettings({ min_free_disk_gb: v ?? 3 })} /></FieldRow>
                <FieldRow label="Clean up now" desc="Deletes old temp files and applies the policy above">
                  <Button size="sm" onClick={() => api.cleanupNow().then(() => toast({ title: "Cleanup queued", level: "info" }))}>Clean up</Button>
                </FieldRow>
              </Section>
            </>
          )}
          {tab === "notifications" && (
            <>
              <Section title="Notify me about">
                {Object.keys(s.notifications).map((k) => <FieldRow key={k} label={k.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase())}>{tg("notifications", k)}</FieldRow>)}
              </Section>
              <Section title="Recent notifications">
                <div className="row" style={{ marginBottom: 6 }}><Button size="sm" className="right" onClick={() => api.readNotifications().then(() => qc.invalidateQueries({ queryKey: ["dashboard"] }))}>Mark all read</Button></div>
                {notifications?.map((n) => (
                  <div key={n.id} className="row small" style={{ padding: "7px 0", borderBottom: "1px solid var(--border)", gap: 10, opacity: n.read ? 0.6 : 1 }}>
                    <Badge color={n.level === "error" ? "red" : n.level === "warning" ? "yellow" : "green"}>{n.category.replace(/_/g, " ")}</Badge>
                    <span className="strong">{n.title}</span><span className="muted ellipsis grow">{n.body}</span><span className="tiny faint">{fmtDateTime(n.created_at)}</span>
                  </div>
                ))}
              </Section>
            </>
          )}
          {tab === "system" && (
            <Section title="GPU & queue" desc="Only one heavy model is kept in VRAM at a time; NVENC rendering can overlap with inference.">
              <FieldRow label="Concurrent GPU jobs"><Num value={s.gpu.max_gpu_jobs} onCommit={(v) => set("gpu", "max_gpu_jobs", v ?? 1)} /></FieldRow>
              <FieldRow label="VRAM safety margin"><Num value={s.gpu.vram_safety_margin_mb} suffix="MB" onCommit={(v) => set("gpu", "vram_safety_margin_mb", v ?? 600)} /></FieldRow>
              <FieldRow label="Fall back to CPU on GPU failure">{tg("gpu", "allow_cpu_fallback")}</FieldRow>
              <FieldRow label="Network jobs in parallel"><Num value={s.queue.network_concurrency} onCommit={(v) => set("queue", "network_concurrency", v ?? 2)} /></FieldRow>
              <FieldRow label="CPU jobs in parallel"><Num value={s.queue.cpu_concurrency} onCommit={(v) => set("queue", "cpu_concurrency", v ?? 2)} /></FieldRow>
              <FieldRow label="Renders in parallel"><Num value={s.queue.render_concurrency} onCommit={(v) => set("queue", "render_concurrency", v ?? 1)} /></FieldRow>
              <FieldRow label="FFmpeg location" desc="Blank = auto-detect"><input className="input" style={{ width: 280 }} defaultValue={s.ffmpeg_path ?? ""} onBlur={(e) => api.patchSettings({ ffmpeg_path: e.target.value.trim() || null })} /></FieldRow>
            </Section>
          )}
          {tab === "debug" && <DebugTab />}
          {tab === "about" && <AboutTab />}
          {!advanced && <div className="tiny faint" style={{ marginTop: 6 }}><Plug size={11} /> Switch to Advanced mode for models, prompts, ranking, captions, reframing, codec and GPU controls.</div>}
        </div>
      </div>
    </div>
  );
}
