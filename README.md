# ShortForge AI

**A local, privacy-first, zero-subscription studio that turns long-form videos into polished vertical
Shorts — automatically.**

Point it at any public YouTube channel, @handle, playlist or video (or a local file). ShortForge
discovers new uploads, downloads them, transcribes and *understands* them with local AI, finds the
strongest self-contained moments, reframes them to 9:16 around the active speaker, adds animated
word-level captions, masters the audio, renders with NVENC, quality-checks the result (repairing what
it can), writes grounded titles and descriptions, and can schedule and upload them — then learns from
real performance data.

Everything runs on one Windows laptop (tuned for RTX 4060/4070 8 GB). No paid AI APIs, no cloud GPUs,
no Redis/Docker.

## Highlights

- **Any public source** — channel URL, `@handle`, `/channel/UC…`, legacy `/c/` & `/user/` URLs, video,
  Shorts/live links, playlists, local files. No allow-lists. Official Data API (optional key), channel
  Atom feeds and yt-dlp behind one provider interface.
- **Robust acquisition** — original-language audio (auto-dubbed tracks are avoided), quality cap
  (1080p / 1440p / 2160p / best), resume, retries, verification, download history.
- **Local AI** — faster-whisper (CUDA fp16, int8 fallback) with word timestamps; Silero VAD; Ollama or
  llama.cpp LLMs (Qwen, Llama, Gemma, Mistral…); OpenCV YuNet faces; PySceneDetect.
- **8-pass clip finder** — sentence-aligned candidates → heuristic scoring → LLM semantic ranking →
  targeted vision → boundary refinement (never mid-word) → hook optimiser → duplicate filtering →
  final ranking, with a transparent per-metric score breakdown.
- **Smart 9:16 reframing** — face tracking, mouth-motion active-speaker detection with hysteresis,
  eased virtual camera (dead-zone holds, anticipatory pans, cuts for big moves, spring smoothing),
  split-screen for two speakers, fit-with-blur for screen content, tasteful punch-ins.
- **Premium captions** — 13 presets rendered by libass, per-word positioning with exact font metrics,
  pop/bounce/fade/slide/karaoke, colour or box highlights, semantic keyword emphasis, safe areas.
- **Automatic QC + auto-repair** — dimensions, FPS, black/frozen frames, loudness, clipping, silence,
  caption overflow & safe zones, timing, jitter, face cut-off, dead air, duplicates.
- **Autopilot** — scheduled channel monitoring, per-source rules, persistent job queue that survives
  restarts, daily slots / interval scheduling, YouTube OAuth uploads (`publishAt` scheduling).
- **Analytics + learning** — real YouTube metrics only; conservative, sample-gated weight updates.
- **Desktop app** — Tauri + React: dashboard with live GPU/VRAM/CPU meters, sources, videos with
  semantic timelines, candidates, Shorts library, non-destructive multi-track editor with live preview,
  queue, schedule, analytics, templates, model manager, first-run wizard.

## Requirements

- Windows 10/11 (the engine also runs on Linux/macOS; the desktop build targets Windows)
- NVIDIA GPU with 6–8 GB+ VRAM recommended (CPU-only works, slower)
- 16 GB RAM recommended
- FFmpeg (with libass), Node.js (used by yt-dlp for YouTube), Python 3.12 via `uv`, Ollama (optional
  but recommended), Rust (only to build the native shell)

## Install

```powershell
git clone https://github.com/shreyjain7/shortforge-ai.git
cd shortforge-ai
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
powershell -ExecutionPolicy Bypass -File scripts\start.ps1
```

`setup.ps1` installs missing prerequisites with winget, creates the Python 3.12 environment, installs
the backend (with CUDA runtime wheels when an NVIDIA GPU is present), builds the UI and the native app.
Models are **not** downloaded automatically: the first-run wizard shows each model's size and installs
what you choose (recommended for 8 GB GPUs: Whisper large-v3-turbo, Qwen 2.5 7B, YuNet).

Without the native shell, the full UI is served at <http://127.0.0.1:8756>.

## Headless use

```powershell
.venv\Scripts\shortforge hardware
.venv\Scripts\shortforge models
.venv\Scripts\shortforge install-model whisper:large-v3-turbo
.venv\Scripts\shortforge run "https://www.youtube.com/watch?v=VIDEO_ID" --shorts 2
.venv\Scripts\shortforge run "@SomeCreator" --videos 1
```

## Publishing to YouTube

Uploads use the official YouTube Data API with your own OAuth client (Google Cloud Console →
Credentials → *Desktop app*; enable *YouTube Data API v3* and optionally *YouTube Analytics API*).
Paste the client JSON in **Settings → Publishing** and connect. Tokens are stored in Windows
Credential Manager.

## Development

```powershell
scripts\dev.ps1            # engine with auto-reload + Vite dev server
scripts\dev.ps1 -Tauri     # same, inside the native window
.venv\Scripts\python -m pytest            # backend unit + integration tests
.venv\Scripts\ruff check backend tests
cd apps\desktop; npm run typecheck; npm run lint; npm test
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the design and
[`docs/STATUS.md`](docs/STATUS.md) for what is implemented and verified.

## Responsible use

ShortForge processes whatever public videos you point it at. You are responsible for having the
rights or permission to download, edit and re-publish content, and for following YouTube's Terms of
Service and the creators' licences. Clip scores are ranking heuristics, not predictions of virality.

## License

MIT. Bundled caption fonts are under the SIL Open Font License (`assets/fonts/OFL-*.txt`).
