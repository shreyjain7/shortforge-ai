import { useQueryClient } from "@tanstack/react-query";
import { createContext, type ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";
import { API } from "./api";
import type { Notification } from "./types";

export interface Toast {
  id: number;
  title: string;
  body?: string | null;
  level: "info" | "success" | "warning" | "error";
  link?: string | null;
}

interface JobProgress {
  progress: number;
  message: string | null;
  video_id: number | null;
  short_id: number | null;
}

interface EventsState {
  connected: boolean;
  progress: Record<number, JobProgress>;
  toasts: Toast[];
  pushToast: (t: Omit<Toast, "id">) => void;
  dismiss: (id: number) => void;
}

const Ctx = createContext<EventsState | null>(null);

let tauriNotify: ((title: string, body?: string) => Promise<void>) | null = null;

async function setupNativeNotifications(): Promise<void> {
  if (!("__TAURI_INTERNALS__" in window)) return;
  try {
    const mod = await import("@tauri-apps/plugin-notification");
    let granted = await mod.isPermissionGranted();
    if (!granted) granted = (await mod.requestPermission()) === "granted";
    if (granted) tauriNotify = async (title, body) => mod.sendNotification({ title, body });
  } catch {
    tauriNotify = null;
  }
}

/** Which react-query caches an event invalidates. */
function keysFor(type: string): string[][] {
  if (type.startsWith("job.")) return [["jobs"], ["stats"], ["dashboard"]];
  if (type === "video.updated") return [["videos"], ["video"], ["dashboard"]];
  if (type === "short.updated") return [["shorts"], ["short"], ["dashboard"], ["uploads"]];
  if (type === "source.updated") return [["sources"]];
  if (type === "candidates.updated") return [["candidates"], ["video"]];
  if (type === "models.updated") return [["models"]];
  if (type === "settings.updated") return [["settings"]];
  if (type === "analytics.updated") return [["analytics"]];
  if (type === "notification") return [["notifications"], ["dashboard"]];
  return [];
}

export function EventsProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [connected, setConnected] = useState(false);
  const [progress, setProgress] = useState<Record<number, JobProgress>>({});
  const [toasts, setToasts] = useState<Toast[]>([]);
  const seq = useRef(1);
  const pending = useRef<Set<string>>(new Set());
  const flushTimer = useRef<number | null>(null);

  const pushToast = useCallback((t: Omit<Toast, "id">) => {
    const id = seq.current++;
    setToasts((prev) => [...prev.slice(-3), { ...t, id }]);
    window.setTimeout(() => setToasts((prev) => prev.filter((x) => x.id !== id)), t.level === "error" ? 9000 : 5500);
  }, []);
  const dismiss = useCallback((id: number) => setToasts((prev) => prev.filter((x) => x.id !== id)), []);

  useEffect(() => {
    void setupNativeNotifications();
  }, []);

  useEffect(() => {
    let es: EventSource | null = null;
    let retry: number | null = null;
    let closed = false;

    const invalidate = (keys: string[][]) => {
      keys.forEach((k) => pending.current.add(JSON.stringify(k)));
      if (flushTimer.current !== null) return;
      // Coalesce bursts of events into one refetch per cache key.
      flushTimer.current = window.setTimeout(() => {
        pending.current.forEach((k) => void qc.invalidateQueries({ queryKey: JSON.parse(k) }));
        pending.current.clear();
        flushTimer.current = null;
      }, 350);
    };

    const connect = () => {
      es = new EventSource(`${API}/events`);
      es.addEventListener("hello", () => {
        setConnected(true);
        invalidate([["dashboard"], ["jobs"], ["stats"]]);
      });
      es.addEventListener("message", (ev) => {
        const data = JSON.parse((ev as MessageEvent).data);
        const type: string = data.type;
        if (type === "job.progress") {
          setProgress((prev) => ({
            ...prev,
            [data.job_id]: { progress: data.progress, message: data.message, video_id: data.video_id, short_id: data.short_id },
          }));
          return;
        }
        if (type === "job.finished" && data.job?.id) {
          setProgress((prev) => {
            const next = { ...prev };
            delete next[data.job.id];
            return next;
          });
          if (data.job.status === "failed") {
            pushToast({ title: `${data.job.label} failed`, body: data.job.error, level: "error" });
          }
        }
        if (type === "notification") {
          const n: Notification = data.notification;
          if (n.show !== false) {
            const level = n.level === "error" ? "error" : n.level === "warning" ? "warning" : "success";
            pushToast({ title: n.title, body: n.body, level, link: n.link });
            if (tauriNotify && document.visibilityState !== "visible") void tauriNotify(n.title, n.body ?? undefined);
          }
        }
        invalidate(keysFor(type));
      });
      es.onerror = () => {
        setConnected(false);
        es?.close();
        if (!closed) retry = window.setTimeout(connect, 2500);
      };
    };
    connect();
    return () => {
      closed = true;
      es?.close();
      if (retry) window.clearTimeout(retry);
    };
  }, [qc, pushToast]);

  return <Ctx.Provider value={{ connected, progress, toasts, pushToast, dismiss }}>{children}</Ctx.Provider>;
}

export function useEvents(): EventsState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("EventsProvider missing");
  return ctx;
}

export function useToast() {
  return useEvents().pushToast;
}
