# Implementation status

Legend: **Verified** = exercised end-to-end against real inputs (real public YouTube videos/channels) on
an RTX 4070 Laptop GPU (8 GB) / Ryzen 7 8845HS / 15 GB RAM. **Tested** = covered by automated tests with
external services mocked. Anything not listed as verified is called out explicitly.

| Milestone | State | Notes |
| --- | --- | --- |
| 1 Foundation | Verified | FastAPI engine, SQLite (WAL) + Alembic, typed settings, redacting rotating JSON logs, hardware detection (CPU/GPU/VRAM/CUDA/NVENC/FFmpeg/storage) with profile recommendations |
| 2 YouTube ingestion | Verified | Handles, channel IDs, video/Shorts/live URLs resolved live; channel scan with dedupe, age/duration filters and per-scan limits; downloader with original-language audio selection, resume and verification. Playlist resolution is tested with mocks |
| 3 Transcription | Verified | faster-whisper large-v3-turbo on CUDA fp16 (≈60× real-time), word timestamps, sentence segmentation, OOM fallback ladder (tested), transcript cache |
| 4 Clip finding | Verified | 8-pass funnel with Qwen 2.5 7B via Ollama and YuNet vision; semantic timeline; grounded titles/hooks/keywords |
| 5 Short generation | Verified | 1080×1920 H.264/AAC via NVENC, face-following virtual camera, libass word-level captions, two-pass loudnorm to −14 LUFS |
| 6 Premium editing | Verified / Tested | Crop smoothing, punch-ins, animated captions (13 presets), fit-with-blur for screen content verified on real video. Split screen, PiP/full B-roll and music ducking verified with synthetic media in tests; B-roll auto-tagging and music tempo/beat analysis tested |
| 7 Quality control | Verified | Media analysis (black/frozen/loudness/clipping/silence), caption overflow & safe areas, jitter, face cut-off, duplicates; auto-repair loop bounded to 2 attempts |
| 8 Autopilot | Verified | Live channel monitoring → download → analysis → Shorts with score/limit rules; persistent queue survives restarts |
| 9 YouTube publishing | Tested | OAuth installed-app flow, resumable uploads with retry, `publishAt` scheduling, playlists, slot planner. **Not yet exercised against a real YouTube account** (requires the user's own OAuth client) |
| 10 Analytics + learning | Tested | Real Data API statistics (+ Analytics API retention when available); conservative weight updates and insights; verified with mocked API responses and synthetic performance data |
| 11 Advanced editor | Verified (UI) | Multi-track non-destructive editor with live preview, trims/splits/deletes that re-time captions, camera pinning, zoom/text/B-roll/music/audio/look controls, versions/undo |
| Desktop app | Verified | Tauri 2 shell starts/stops the engine, native notifications; all pages checked in a real WebView |

## Known limitations

- Object detection (YOLO) and OCR are not implemented; screen content is detected heuristically
  (edge/line density) and faces drive framing.
- Speaker diarization is visual (mouth motion + VAD), not audio-based.
- Duplicate detection uses source ranges, transcript shingles, text embeddings and per-second
  visual hashes; there is no audio fingerprinting.
- Colour emoji in captions are not supported by libass.
- The Windows installer packages the desktop shell; the Python engine runs from the cloned repo
  (created by `scripts/setup.ps1`) rather than being frozen into the installer.
