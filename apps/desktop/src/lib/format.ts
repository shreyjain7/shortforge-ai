export function fmtDuration(sec: number | null | undefined): string {
  if (sec === null || sec === undefined || !isFinite(sec)) return "—";
  const s = Math.max(0, Math.round(sec));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}` : `${m}:${String(r).padStart(2, "0")}`;
}

export function fmtTimecode(sec: number): string {
  const s = Math.max(0, sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = (s % 60).toFixed(1).padStart(4, "0");
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${r}`;
}

export function fmtBytes(bytes: number | null | undefined): string {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = bytes;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}

export function fmtCompact(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(n);
}

export function relTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  const diff = (t - Date.now()) / 1000;
  const abs = Math.abs(diff);
  const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  if (abs < 45) return diff < 0 ? "just now" : "in a moment";
  if (abs < 3600) return rtf.format(Math.round(diff / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(diff / 3600), "hour");
  if (abs < 86400 * 30) return rtf.format(Math.round(diff / 86400), "day");
  if (abs < 86400 * 365) return rtf.format(Math.round(diff / (86400 * 30)), "month");
  return rtf.format(Math.round(diff / (86400 * 365)), "year");
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function scoreColor(score: number | null | undefined): string {
  if (score === null || score === undefined) return "var(--text-3)";
  if (score >= 80) return "#34d399";
  if (score >= 65) return "#a3e635";
  if (score >= 50) return "#fbbf24";
  return "#f87171";
}

export const LABEL_COLORS: Record<string, string> = {
  INTRO: "#475569",
  STORY: "#7c3aed",
  EXPLANATION: "#2563eb",
  DISCUSSION: "#334155",
  HUMOR: "#db2777",
  HIGH_IMPACT: "#ea580c",
  PAYOFF: "#059669",
  TUTORIAL: "#0891b2",
  OPINION: "#9333ea",
  QA: "#0284c7",
  SPONSOR: "#6b7280",
  OUTRO: "#475569",
  TRANSITION: "#3f3f46",
};

export const STATUS_BADGE: Record<string, string> = {
  draft: "",
  rendering: "blue",
  review: "yellow",
  ready: "green",
  scheduled: "violet",
  uploading: "cyan",
  published: "green",
  failed: "red",
  queued: "",
  running: "blue",
  paused: "yellow",
  done: "green",
  cancelled: "",
  PASS: "green",
  WARNING: "yellow",
  FAIL: "red",
  new: "",
  ignored: "",
  processing: "blue",
  analyzed: "green",
  downloading: "blue",
  uploaded: "green",
};

export function titleCase(s: string): string {
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
