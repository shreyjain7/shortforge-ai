# Implementation status

Updated as milestones land. "Verified" means exercised against real inputs on an RTX 4070 Laptop GPU.

- [x] Milestone 1 — foundation: repo layout, FastAPI, SQLite + Alembic, settings, structured logging, hardware detection
- [x] Milestone 2 — YouTube ingestion: URL/handle/channel/playlist/video/local-file resolution, yt-dlp + Data API + Atom feed discovery, downloader with resume/retry/verification (verified on real public videos)
- [x] Milestone 3 — transcription: faster-whisper on CUDA with word timestamps, OOM fallback ladder, transcript DB (verified)
- [x] Milestone 4 — clip finding: scenes, VAD, 8-pass funnel with local LLM ranking + vision (verified)
- [x] Milestone 5 — Short generation: 9:16 reframing, face tracking, animated captions, loudness mastering, NVENC render (verified: playable 1080x1920 H.264/AAC)
- [ ] Desktop UI (Tauri + React) — in progress
