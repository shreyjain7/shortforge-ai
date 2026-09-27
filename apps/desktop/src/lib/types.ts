export interface Channel {
  id: string;
  name: string;
  handle: string | null;
  url: string | null;
  avatar_url: string | null;
  banner_url: string | null;
  subscriber_count: number | null;
  video_count: number | null;
  description: string;
}

export interface SourceStats {
  videos: number;
  processed: number;
  shorts: number;
  processing: number;
}

export interface Source {
  id: number;
  kind: "channel" | "handle" | "video" | "playlist" | "local_file";
  input: string;
  url: string;
  title: string;
  thumbnail_url: string | null;
  channel: Channel | null;
  enabled: boolean;
  auto_scan: boolean;
  priority: number;
  scan_interval_min: number | null;
  max_videos_per_scan: number;
  min_duration_s: number | null;
  max_duration_s: number | null;
  max_video_age_days: number | null;
  max_shorts_per_video: number | null;
  max_shorts_per_day: number | null;
  min_candidate_score: number | null;
  short_min_s: number | null;
  short_max_s: number | null;
  preferred_style: string | null;
  reframe_mode: string | null;
  upload_destination: string | null;
  schedule_strategy: string | null;
  auto_download: boolean;
  process_existing: boolean;
  status: string;
  last_error: string | null;
  last_scan_at: string | null;
  next_scan_at: string | null;
  created_at: string;
  stats: SourceStats;
}

export interface VideoMetaPreview {
  id: string;
  title: string;
  url: string;
  duration: number | null;
  published_at: string | null;
  thumbnail_url: string | null;
  view_count: number | null;
}

export interface ResolvePreview {
  kind: Source["kind"];
  value: string;
  url: string;
  title: string;
  thumbnail_url: string | null;
  video_count: number | null;
  channel: (Channel & { description?: string }) | null;
  recent_videos: VideoMetaPreview[];
}

export interface Job {
  id: number;
  type: string;
  label: string;
  status: "queued" | "running" | "paused" | "done" | "failed" | "cancelled";
  resource: string;
  priority: number;
  progress: number;
  message: string | null;
  error: string | null;
  error_detail: string | null;
  retry_count: number;
  max_retries: number;
  video_id: number | null;
  short_id: number | null;
  source_id: number | null;
  payload: Record<string, unknown>;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  not_before: string | null;
  logs?: string;
  result?: Record<string, unknown> | null;
}

export interface TimelineSeg {
  start: number;
  end: number;
  label: string;
  summary: string | null;
  interest: number | null;
  energy: number | null;
  source: string;
}

export interface ScoreRow {
  metric: string;
  label: string;
  value: number;
  kind: "score" | "penalty";
  source: string;
  weight: number;
}

export interface Candidate {
  id: number;
  video_id: number;
  start: number;
  end: number;
  duration: number;
  text: string;
  title: string | null;
  hook_text: string | null;
  reasoning: string | null;
  labels: { hook_type?: string; category?: string; topic?: string };
  keywords: string[];
  heuristic_score: number;
  llm_score: number | null;
  score: number;
  rank: number | null;
  status: string;
  favorite: boolean;
  duplicate_of: number | null;
  similarity: number | null;
  pass_reached: number;
  llm_model: string | null;
  created_at: string;
  vision: Record<string, any>;
  notes: string[];
  metrics?: Record<string, number>;
  scores?: ScoreRow[];
  video?: { id: number; title: string; channel_name: string | null; thumbnail_url: string | null; proxy_url: string | null };
}

export interface Video {
  id: number;
  youtube_id: string | null;
  source_id: number | null;
  channel_id: string | null;
  channel_name: string | null;
  title: string;
  description: string;
  thumbnail_url: string | null;
  duration_s: number | null;
  published_at: string | null;
  source_url: string | null;
  has_local: boolean;
  proxy_url: string | null;
  width: number | null;
  height: number | null;
  fps: number | null;
  file_size: number | null;
  view_count: number | null;
  language: string | null;
  download_status: string;
  processing_status: string;
  transcript_status: string;
  scenes_status: string;
  analysis_status: string;
  stage: string | null;
  error: string | null;
  analysis: Record<string, any>;
  discovered_at: string;
  candidates?: number | Candidate[];
  shorts?: number | { id: number; status: string; title: string }[];
  timeline?: TimelineSeg[];
  scene_cuts?: number[];
  jobs?: Job[];
  transcript?: { model: string; language: string; device: string; elapsed_s: number } | null;
  source?: { id: number; title: string } | null;
}

export interface Upload {
  id: number;
  short_id: number;
  youtube_video_id: string | null;
  status: string;
  scheduled_at: string | null;
  publish_at: string | null;
  visibility: string;
  made_for_kids: boolean;
  playlist_id: string | null;
  title: string;
  description: string | null;
  tags: string[];
  progress: number;
  bytes_sent: number | null;
  speed_bps: number | null;
  processing_status: string | null;
  error: string | null;
  attempts: number;
  started_at: string | null;
  finished_at: string | null;
  url: string | null;
  short?: Short | null;
}

export interface QCIssue {
  check: string;
  level: "WARNING" | "FAIL";
  message: string;
  repair: string | null;
}

export interface Short {
  id: number;
  candidate_id: number | null;
  video_id: number | null;
  title: string;
  description: string | null;
  hashtags: string[];
  status: string;
  caption_preset: string | null;
  reframe_mode: string | null;
  render_profile: string | null;
  start: number;
  end: number;
  duration: number | null;
  width: number | null;
  height: number | null;
  score: number | null;
  qc_status: "PASS" | "WARNING" | "FAIL" | null;
  repair_attempts: number;
  favorite: boolean;
  error: string | null;
  created_at: string;
  updated_at: string;
  video_url: string | null;
  cover_url: string | null;
  file_size: number | null;
  output_path: string | null;
  title_options: string[];
  metadata_source: string | null;
  video?: { id: number; title: string; channel_name: string | null; source_id: number | null };
  qc_report?: { status: string; issues: QCIssue[]; metrics: Record<string, any>; checks_run: string[]; repairs?: string[] } | null;
  metadata?: { titles: string[]; description: string; hashtags: string[]; keywords: string[]; internal_tags: string[]; mode: string; source: string; model: string | null } | null;
  render_report?: Record<string, any> | null;
  uploads?: Upload[];
  renders?: { id: number; status: string; encoder: string | null; elapsed_s: number | null; file_size: number | null; timeline_version: number | null; error: string | null; finished_at: string | null }[];
  timeline?: EditTimeline | null;
  timeline_version?: number | null;
  versions?: { version: number; origin: string; created_at: string }[];
  active_job?: boolean;
  analytics?: { views: number | null; likes: number | null; comments: number | null; fetched_at: string } | null;
}

export interface CaptionWord { text: string; start: number; end: number; emphasis: boolean; speaker: number | null; src_start?: number | null; src_end?: number | null }
export interface CropKey { t: number; cx: number; cy: number; zoom: number }
export interface LayoutSegment { start: number; end: number; layout: "crop" | "fit" | "split" | "pip"; secondary: CropKey[]; note: string | null }
export interface ZoomEvent { start: number; end: number; scale: number; ease_in: number; ease_out: number; reason: string | null }
export interface EditTimeline {
  schema_version: number;
  source_path: string;
  source_width: number;
  source_height: number;
  fps: number;
  width: number;
  height: number;
  ranges: { start: number; end: number }[];
  layouts: LayoutSegment[];
  crop: CropKey[];
  zooms: ZoomEvent[];
  captions: { enabled: boolean; preset: string; words: CaptionWord[]; max_words: number | null; vertical_position: number | null; overrides: Record<string, any> };
  overlays: { text: string; start: number; end: number; kind: string }[];
  broll: { path: string; start: number; end: number; source_start: number; mode: string; label: string | null }[];
  broll_suggestions?: { path: string; start: number; end: number; source_start: number; mode: string; label: string | null }[];
  music: { path: string; volume_db: number; duck_db: number; offset: number; fade_in: number; fade_out: number } | null;
  audio: { target_lufs: number; true_peak: number; fade_ms: number; compressor: boolean; eq: boolean; denoise: boolean; gain_db: number };
  enhance: { sharpen: boolean; vignette: boolean; contrast: boolean; color: boolean; denoise: boolean };
  safe_area: { top: number; bottom: number; left: number; right: number };
  speed: number;
  notes: string[];
}

export interface CaptionPreset {
  name: string;
  description: string;
  font_file: string;
  font_name: string;
  font_size: number;
  uppercase: boolean;
  primary_color: string;
  highlight_color: string;
  emphasis_color: string;
  outline_color: string;
  outline_width: number;
  highlight_mode: string;
  animation: string;
  position: number;
  max_words: number;
  builtin: boolean;
  [key: string]: unknown;
}

export interface ModelItem {
  id: string;
  kind: "whisper" | "llm" | "vision" | "embedding";
  name: string;
  purpose: string;
  size_mb: number;
  vram_mb: number;
  source: string;
  recommended_for: string[];
  installed: boolean;
  location: string;
  available: boolean;
  installed_size_mb: number;
}

export interface Notification {
  id: number;
  category: string;
  level: string;
  title: string;
  body: string | null;
  read: boolean;
  link: string | null;
  created_at: string;
  show?: boolean;
}

export type Settings = Record<string, any>;
