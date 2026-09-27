import type {
  Candidate,
  CaptionPreset,
  EditTimeline,
  Job,
  ModelItem,
  Notification,
  ResolvePreview,
  Settings,
  Short,
  Source,
  Upload,
  Video,
} from "./types";

export const API_ORIGIN: string =
  (import.meta.env.VITE_API_ORIGIN as string | undefined) ??
  (location.port === "8756" ? location.origin : "http://127.0.0.1:8756");
export const API = `${API_ORIGIN}/api`;

export function mediaUrl(path: string | null | undefined): string | undefined {
  if (!path) return undefined;
  if (path.startsWith("http")) return path;
  return `${API_ORIGIN}${path}`;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API}${path}`, {
      method,
      headers: {
        "X-ShortForge-Client": "desktop",
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, "Cannot reach the ShortForge engine. It may still be starting.");
  }
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const data = await res.json();
      message = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, message);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const get = <T>(p: string) => request<T>("GET", p);
const post = <T>(p: string, b: unknown = {}) => request<T>("POST", p, b);
const patch = <T>(p: string, b: unknown) => request<T>("PATCH", p, b);
const put = <T>(p: string, b: unknown) => request<T>("PUT", p, b);
const del = <T>(p: string) => request<T>("DELETE", p);

function qs(params: Record<string, unknown>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") u.set(k, String(v));
  }
  const s = u.toString();
  return s ? `?${s}` : "";
}

export interface Dashboard {
  counts: Record<string, number>;
  active: Job[];
  recent_shorts: Short[];
  storage: { root: string; folders: Record<string, number>; total_bytes: number; disk_free: number; disk_total: number };
  unread_notifications: number;
}

export interface LiveStats {
  live: {
    cpu_percent: number; ram_used_gb: number; ram_total_gb: number; ram_percent: number;
    gpu_util: number | null; vram_used_mb: number | null; vram_total_mb: number | null; gpu_temp_c: number | null;
    gpu_power_w: number | null; encoder_util: number | null;
  };
  queue: { counts: Record<string, number>; paused: boolean; running: Job[] };
  models_loaded: { name: string; vram_mb: number; idle_s: number }[];
}

export const api = {
  health: () => get<{ status: string; version: string }>("/health"),
  hardware: (refresh = false) => get<Record<string, any>>(`/system/hardware${qs({ refresh })}`),
  stats: () => get<LiveStats>("/system/stats"),
  dashboard: () => get<Dashboard>("/system/dashboard"),
  dependencies: () => get<Record<string, any>>("/system/dependencies"),
  logs: (lines = 300, level?: string) => get<Record<string, any>[]>(`/system/logs${qs({ lines, level })}`),
  storage: (force = false) => get<Dashboard["storage"]>(`/system/storage${qs({ force })}`),

  settings: () => get<Settings>("/settings"),
  patchSettings: (p: Settings) => patch<Settings>("/settings", p),
  resetSettings: (section: string) => post<Settings>(`/settings/reset/${section}`),
  secrets: () => get<Record<string, any>>("/secrets"),
  setSecret: (name: string, value: string | null) => put(`/secrets/${name}`, { value }),
  storageLocation: () => get<{ current: string; configured: string | null }>("/storage/location"),
  setStorageLocation: (data_dir: string) => post<{ data_dir: string; restart_required: boolean }>("/storage/location", { data_dir }),
  completeOnboarding: (body: Settings) => post<Settings>("/onboarding/complete", body),
  notifications: () => get<Notification[]>("/notifications"),
  readNotifications: () => post("/notifications/read-all"),

  resolveSource: (input: string) => post<ResolvePreview>("/sources/resolve", { input }),
  sources: () => get<Source[]>("/sources"),
  addSource: (body: Partial<Source> & { input: string; scan_now?: boolean }) => post<Source>("/sources", body),
  patchSource: (id: number, body: Record<string, unknown>) => patch<Source>(`/sources/${id}`, body),
  deleteSource: (id: number) => del(`/sources/${id}`),
  scanSource: (id: number) => post<{ job_id: number }>(`/sources/${id}/scan`),
  processExisting: (id: number, limit = 5) => post<{ queued: number[] }>(`/sources/${id}/process-existing`, { limit }),

  videos: (params: Record<string, unknown> = {}) => get<{ total: number; items: Video[] }>(`/videos${qs(params)}`),
  video: (id: number) => get<Video>(`/videos/${id}`),
  transcript: (id: number, words = false) => get<Record<string, any>>(`/videos/${id}/transcript${qs({ words })}`),
  processVideo: (id: number) => post(`/videos/${id}/process`),
  reanalyze: (id: number, retranscribe = false) => post(`/videos/${id}/reanalyze${qs({ retranscribe })}`),
  findMore: (id: number) => post(`/videos/${id}/find-more`),
  generateShorts: (id: number, count = 3) => post<{ short_ids: number[] }>(`/videos/${id}/generate`, { count }),
  deleteVideo: (id: number, deleteFiles = false) => del(`/videos/${id}${qs({ delete_files: deleteFiles })}`),
  importLocal: (path: string) => post<{ video_id: number }>("/videos/import", { path }),

  candidates: (params: Record<string, unknown> = {}) => get<{ total: number; items: Candidate[] }>(`/candidates${qs(params)}`),
  candidate: (id: number) => get<Candidate>(`/candidates/${id}`),
  generateCandidate: (id: number) => post<{ short_id: number }>(`/candidates/${id}/generate`),
  rejectCandidate: (id: number) => post<Candidate>(`/candidates/${id}/reject`),
  favoriteCandidate: (id: number) => post<Candidate>(`/candidates/${id}/favorite`),
  editCandidate: (id: number, start: number, end: number) => patch<Candidate>(`/candidates/${id}`, { start, end }),

  shorts: (params: Record<string, unknown> = {}) =>
    get<{ total: number; counts: Record<string, number>; items: Short[] }>(`/shorts${qs(params)}`),
  short: (id: number) => get<Short>(`/shorts/${id}`),
  patchShort: (id: number, body: Record<string, unknown>) => patch<Short>(`/shorts/${id}`, body),
  saveTimeline: (id: number, tl: EditTimeline, render = true) =>
    put<{ version: number; job_id: number | null }>(`/shorts/${id}/timeline${qs({ render })}`, tl),
  revertTimeline: (id: number, version: number) => post(`/shorts/${id}/revert/${version}`),
  renderShort: (id: number, rebuild = false) => post(`/shorts/${id}/render${qs({ rebuild })}`),
  regenerateMetadata: (id: number, mode: string) => post(`/shorts/${id}/metadata`, { mode }),
  scheduleShort: (id: number, at?: string | null, visibility?: string) =>
    post<{ upload_id: number }>(`/shorts/${id}/schedule`, { at: at ?? null, visibility }),
  deleteShort: (id: number, deleteFile = false) => del(`/shorts/${id}${qs({ delete_file: deleteFile })}`),
  captionLayout: (id: number) => get<Record<string, any>>(`/shorts/${id}/caption-layout`),

  jobs: (params: Record<string, unknown> = {}) =>
    get<{ items: Job[]; counts: Record<string, number>; stages: { type: string; label: string; active: number }[]; paused: boolean }>(`/jobs${qs(params)}`),
  job: (id: number) => get<Job>(`/jobs/${id}`),
  jobAction: (id: number, action: "cancel" | "retry" | "pause" | "resume" | "top") => post(`/jobs/${id}/${action}`),
  pauseQueue: (paused: boolean) => post(`/queue/${paused ? "pause" : "resume"}`),
  clearFinished: () => del("/jobs/finished"),

  uploads: (status?: string) => get<Upload[]>(`/uploads${qs({ status })}`),
  patchUpload: (id: number, body: Record<string, unknown>) => patch<Upload>(`/uploads/${id}`, body),
  cancelUpload: (id: number) => del(`/uploads/${id}`),
  schedulePreview: (count = 6) => get<{ next_slots: string[]; strategy: string }>(`/schedule/preview${qs({ count })}`),

  youtubeAccount: () => get<Record<string, any>>("/accounts/youtube"),
  youtubeClient: (json: string) => post("/accounts/youtube/client", { json }),
  youtubeConnect: () => post<{ status: string; message: string }>("/accounts/youtube/connect"),
  youtubeDisconnect: () => post("/accounts/youtube/disconnect"),

  analytics: () => get<Record<string, any>>("/analytics/summary"),
  refreshAnalytics: () => post("/analytics/refresh"),
  learning: () => get<Record<string, any>>("/analytics/learning"),
  runLearning: () => post("/analytics/learning/run"),
  resetLearning: () => post("/analytics/learning/reset"),
  setWeights: (weights: Record<string, number>) => put("/analytics/weights", { weights }),

  models: () => get<{ items: ModelItem[]; active: Record<string, string>; recommended: Record<string, string>; vram_mb: number }>("/models"),
  installModel: (id: string) => post<{ job_id: number }>(`/models/${encodeURIComponent(id)}/install`),
  removeModel: (id: string) => del(`/models/${encodeURIComponent(id)}`),
  activateModel: (id: string) => post(`/models/${encodeURIComponent(id)}/activate`),

  captionPresets: () => get<CaptionPreset[]>("/templates/captions"),
  saveCaptionPreset: (p: Partial<CaptionPreset>) => post<CaptionPreset>("/templates/captions", p),
  deleteCaptionPreset: (name: string) => del(`/templates/captions/${encodeURIComponent(name)}`),
  fonts: () => get<string[]>("/templates/fonts"),
  presetPreviewUrl: (name: string) => `${API}/templates/captions/${encodeURIComponent(name)}/preview.png`,
  reveal: (path: string) => post("/media/reveal", { path }),
  cleanupNow: () => post("/system/cleanup"),
};
