import { motion } from "framer-motion";
import { AlertTriangle, CheckCircle2, Cpu, Loader2, RotateCcw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { runEngineSetup, type EngineStatus, type SetupEvent } from "../lib/native";
import { Button } from "./ui";

const STEPS = ["Installing Python 3.12", "Creating the engine environment", "Installing the AI engine", "Starting the engine"];

/** First launch (or after an app update): installs/upgrades the local engine with live progress. */
export function EngineSetup({ status, onReady }: { status: EngineStatus; onReady: () => void }) {
  const [step, setStep] = useState(0);
  const [title, setTitle] = useState("Preparing…");
  const [log, setLog] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const logRef = useRef<HTMLDivElement>(null);
  const upgrading = status.mode === "managed";

  useEffect(() => {
    let stop: (() => void) | undefined;
    let cancelled = false;
    setError(null);
    setLog([]);
    runEngineSetup((e: SetupEvent) => {
      if (cancelled) return;
      setStep(e.step);
      setTitle(e.title);
      if (e.detail) setLog((l) => [...l.slice(-300), e.detail]);
      if (e.error) setError(e.error);
      if (e.done) onReady();
    }).then((u) => { stop = u; }).catch((err) => setError(String(err)));
    return () => { cancelled = true; stop?.(); };
  }, [attempt, onReady]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [log]);

  const pct = Math.round((Math.max(0, step - 1) / STEPS.length) * 100);
  return (
    <div className="center" style={{ minHeight: "100vh", padding: 24,
      background: "radial-gradient(1000px 600px at 70% -10%, rgba(99,102,241,.16), transparent 60%), var(--bg)" }}>
      <motion.div className="card" style={{ width: "min(720px, 100%)", padding: 28 }} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}>
        <div className="row" style={{ gap: 14 }}>
          <div className="brand-logo" style={{ width: 48, height: 48, borderRadius: 14 }}><Cpu size={22} color="#fff" /></div>
          <div>
            <h2 style={{ margin: 0 }}>{upgrading ? "Updating the ShortForge engine" : "Setting up ShortForge"}</h2>
            <div className="small muted">
              {upgrading
                ? `Engine ${status.installed_version ?? "?"} → ${status.bundled_version ?? status.app_version}. Your projects and settings are kept.`
                : "One-time setup: installs Python and the local AI engine. Nothing is uploaded; everything stays on this PC."}
            </div>
          </div>
        </div>
        <div className="col" style={{ gap: 10, marginTop: 22 }}>
          {STEPS.map((s, i) => {
            const n = i + 1;
            const state = error && n === step ? "error" : n < step || (n === step && step === 4 && !error && title === "Ready") ? "done" : n === step ? "active" : "todo";
            return (
              <div key={s} className="row" style={{ gap: 10, opacity: state === "todo" ? 0.45 : 1 }}>
                {state === "done" ? <CheckCircle2 size={17} color="var(--success)" /> : state === "active" ? <Loader2 size={17} className="spin" color="var(--accent-2)" />
                  : state === "error" ? <AlertTriangle size={17} color="var(--danger)" /> : <span style={{ width: 17, height: 17, borderRadius: 17, border: "2px solid var(--border-2)" }} />}
                <span className={state === "active" ? "strong" : ""}>{n === step && title ? title : s}</span>
              </div>
            );
          })}
        </div>
        <div className="bar" style={{ marginTop: 18 }}><span style={{ width: `${error ? pct : Math.max(4, pct)}%` }} /></div>
        <div className="row tiny faint" style={{ marginTop: 6 }}>
          <span>Step {Math.max(1, step)} of {STEPS.length}</span>
          <span className="right">{step === 3 ? "The first install downloads ~2 GB of AI libraries; later updates are much smaller." : ""}</span>
        </div>
        <div ref={logRef} className="log" style={{ marginTop: 14, height: 180 }}>{log.length ? log.join("\n") : "Waiting for output…"}</div>
        {error && (
          <div className="card card-pad small" style={{ marginTop: 14, borderColor: "rgba(248,113,113,.4)" }}>
            <div className="strong" style={{ color: "var(--danger)" }}>Setup did not finish</div>
            <div className="mono" style={{ whiteSpace: "pre-wrap", marginTop: 6, maxHeight: 140, overflow: "auto" }}>{error}</div>
            <div className="muted" style={{ marginTop: 6 }}>Check your internet connection and free disk space, then retry. Progress already downloaded is reused.</div>
            <Button style={{ marginTop: 10 }} variant="primary" onClick={() => setAttempt((a) => a + 1)}><RotateCcw size={14} /> Retry</Button>
          </div>
        )}
      </motion.div>
    </div>
  );
}
