import { AnimatePresence, motion } from "framer-motion";
import { Download, RefreshCw, Sparkles, X } from "lucide-react";
import { createContext, type ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { checkForUpdate, type AvailableUpdate, isTauri } from "../lib/native";
import { fmtBytes } from "../lib/format";
import { Button, Progress } from "./ui";

type Phase = "idle" | "checking" | "available" | "downloading" | "installing" | "uptodate" | "error";

interface UpdaterState {
  phase: Phase;
  update: AvailableUpdate | null;
  downloaded: number;
  total: number | null;
  error: string | null;
  check: (manual?: boolean) => Promise<void>;
  install: () => Promise<void>;
  dismissed: boolean;
  dismiss: () => void;
}

const Ctx = createContext<UpdaterState | null>(null);

export function UpdaterProvider({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [update, setUpdate] = useState<AvailableUpdate | null>(null);
  const [downloaded, setDownloaded] = useState(0);
  const [total, setTotal] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState(false);

  const check = useCallback(async (manual = false) => {
    if (!isTauri()) {
      if (manual) { setPhase("error"); setError("Updates are managed by the desktop app."); }
      return;
    }
    setPhase("checking");
    setError(null);
    try {
      const u = await checkForUpdate();
      setUpdate(u);
      setPhase(u ? "available" : "uptodate");
      if (u) setDismissed(false);
    } catch (e) {
      setPhase(manual ? "error" : "idle");
      setError(String(e));
    }
  }, []);

  const install = useCallback(async () => {
    if (!update) return;
    setPhase("downloading");
    setDownloaded(0);
    try {
      await update.install((d, t) => {
        setDownloaded(d);
        setTotal(t);
        if (t && d >= t) setPhase("installing");
      });
    } catch (e) {
      setPhase("error");
      setError(String(e));
    }
  }, [update]);

  useEffect(() => {
    const first = window.setTimeout(() => void check(), 6000);
    const every = window.setInterval(() => void check(), 6 * 3600 * 1000);
    return () => { window.clearTimeout(first); window.clearInterval(every); };
  }, [check]);

  return (
    <Ctx.Provider value={{ phase, update, downloaded, total, error, check, install, dismissed, dismiss: () => setDismissed(true) }}>
      {children}
    </Ctx.Provider>
  );
}

export function useUpdater(): UpdaterState {
  const c = useContext(Ctx);
  if (!c) throw new Error("UpdaterProvider missing");
  return c;
}

export function UpdateProgress() {
  const u = useUpdater();
  const pct = u.total ? u.downloaded / u.total : 0;
  if (u.phase === "downloading" || u.phase === "installing") {
    return (
      <div className="col" style={{ gap: 6 }}>
        <div className="row small">
          <span className="strong">{u.phase === "installing" ? "Installing update — the app will restart" : `Downloading ShortForge ${u.update?.version}`}</span>
          <span className="right mono">{u.total ? `${Math.round(pct * 100)}%` : ""}</span>
        </div>
        <Progress value={u.phase === "installing" ? 1 : pct} />
        <div className="tiny faint">{fmtBytes(u.downloaded)}{u.total ? ` of ${fmtBytes(u.total)}` : ""}</div>
      </div>
    );
  }
  return null;
}

export function ReleaseNotes({ notes }: { notes: string }) {
  const lines = notes.split("\n").map((l) => l.trim()).filter(Boolean);
  return (
    <div className="col small" style={{ gap: 4, maxHeight: 180, overflowY: "auto" }}>
      {lines.map((l, i) => l.startsWith("#") ? <div key={i} className="strong" style={{ marginTop: 6 }}>{l.replace(/^#+\s*/, "")}</div>
        : <div key={i} className="muted">{l.startsWith("-") || l.startsWith("*") ? "• " + l.slice(1).trim() : l}</div>)}
    </div>
  );
}

/** Floating card shown when an update is available / downloading. */
export function UpdateBanner() {
  const u = useUpdater();
  const show = !u.dismissed && ["available", "downloading", "installing"].includes(u.phase) && u.update;
  return (
    <AnimatePresence>
      {show && (
        <motion.div className="glass" initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 20 }}
          style={{ position: "fixed", left: 20, bottom: 20, width: 380, zIndex: 70, borderRadius: 16, padding: 16, boxShadow: "var(--shadow-pop)" }}>
          <div className="row" style={{ gap: 10 }}>
            <div className="brand-logo" style={{ width: 32, height: 32 }}><Sparkles size={16} color="#fff" /></div>
            <div className="grow">
              <div className="strong">ShortForge {u.update!.version} is available</div>
              <div className="tiny faint">You have {u.update!.currentVersion}</div>
            </div>
            {u.phase === "available" && <Button size="sm" variant="ghost" icon onClick={u.dismiss}><X size={14} /></Button>}
          </div>
          {u.phase === "available" ? (
            <>
              {u.update!.notes && <div style={{ marginTop: 10 }}><ReleaseNotes notes={u.update!.notes} /></div>}
              <div className="row" style={{ marginTop: 12, gap: 8 }}>
                <Button variant="primary" size="sm" onClick={() => void u.install()}><Download size={13} /> Update now</Button>
                <Button size="sm" variant="ghost" onClick={u.dismiss}>Later</Button>
              </div>
            </>
          ) : (
            <div style={{ marginTop: 12 }}><UpdateProgress /></div>
          )}
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export function CheckForUpdatesButton() {
  const u = useUpdater();
  return (
    <Button size="sm" onClick={() => void u.check(true)} loading={u.phase === "checking"}>
      <RefreshCw size={13} /> Check for updates
    </Button>
  );
}
