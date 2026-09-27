# ShortForge AI — Architecture

## Processes

```
┌──────────────────────────── Tauri shell (Rust) ────────────────────────────┐
│  WebView2 ── React/TS UI ── fetch + SSE ──►  127.0.0.1:8756 (loopback only)  │
│  starts/stops the engine process, native notifications                      │
└────────────────────────────────────────────────────────────────────────────┘
                                   │
┌──────────────────────── Python engine (FastAPI) ───────────────────────────┐
│  REST API  ·  SSE event bus  ·  persistent job queue  ·  Autopilot loop      │
│  engines/: youtube · downloader · transcription · scenes · audio · vision    │
│            tracking · clip_detection · ranking · captions · reframing        │
│            editing · rendering · quality_control · metadata · publishing     │
│            analytics · learning · llm · media                                │
│  SQLite (WAL) + Alembic  ·  ShortForgeData/ on disk  ·  OS keyring secrets   │
└────────────────────────────────────────────────────────────────────────────┘
        │ subprocess              │ HTTP                  │ in-process
     FFmpeg/NVENC            Ollama / llama.cpp     faster-whisper (CUDA), OpenCV, ONNX
```

The UI also runs in any browser at `http://127.0.0.1:8756` (the engine serves the built UI).

## Repository layout

| Path | Contents |
| --- | --- |
| `apps/desktop/` | Tauri 2 shell (`src-tauri/`) and the React + TypeScript UI (`src/`) |
| `backend/shortforge/api/` | FastAPI routers + explicit serializers |
| `backend/shortforge/core/` | settings, paths, logging (redacting), hardware, GPU/model lifecycle, secrets, model registry |
| `backend/shortforge/database/` | SQLAlchemy models, session management, Alembic migrations |
| `backend/shortforge/workers/` | job queue, pipeline stages, autopilot, event bus |
| `backend/shortforge/engines/` | independent engines (no DB access) — see below |
| `presets/captions/` | built-in caption styles (JSON) |
| `assets/fonts/` | OFL caption fonts |
| `tests/` | unit + integration tests (YouTube mocked, real FFmpeg rendering) |

Python engines are deliberately DB-agnostic: they take plain data and return plain data. Only
`workers/stages.py` moves data between the database and the engines, which keeps engines testable
and replaceable.

## Pipeline (persistent jobs)

```
scan_source ─► download_video ─► prepare_media (audio 16k, 540p proxy, thumbnail)
                                   ├─► transcribe (GPU)      ─┐
                                   └─► detect_scenes (CPU)   ─┴─► analyze_video (audio features + semantic timeline)
                                                                   └─► find_clips (8-pass funnel)
                                                                         └─► render_short ─► qc_short ─► short_metadata
                                                                                  ▲  auto-repair  │           └─► schedule ─► upload_short
                                                                                  └───────────────┘
refresh_analytics ─► learning_update           install_model           storage_cleanup
```

* Jobs live in SQLite with status, progress, message, error, retry count, timestamps and bounded logs.
* Resource classes (`network`, `cpu`, `gpu`, `render`, `io`) have independent concurrency limits. GPU
  inference is serialised; NVENC rendering can overlap with it.
* Follow-up jobs are only created when a handler *succeeds* (deferred enqueue).
* Retryable errors back off exponentially; permanent errors stop with a readable message.
* On start-up, interrupted jobs are re-queued, so a crash or reboot loses no work.

## Clip finding (8 passes)

1. **Transcript + audio** — faster-whisper word timestamps; Silero VAD; RMS energy; silences.
2. **Scenes** — PySceneDetect content detector on the proxy, plus per-second visual activity from the
   same decode pass.
3. **Candidates** — every sentence-aligned span inside the duration window, scored with fast heuristics
   (hook, curiosity, standalone context, emotion, payoff, density, speech quality, completeness,
   novelty, pacing, …) and penalties (dead air, missing context, abrupt ending, sponsor reads, …).
   Greedy non-max suppression keeps a diverse shortlist.
4. **LLM ranking** — the local LLM rates the shortlist with a JSON schema, proposes trims/extensions,
   a grounded title, an optional on-screen hook and verbatim keywords. All generated text is validated
   against the transcript.
5. **Vision** — face presence/size, tracks, motion, sharpness and screen-likeness for the top clips.
6. **Boundaries** — cuts land in gaps between words with a lead-in and a tail; snap to nearby hard
   cuts; hook optimiser can drop weak opening sentences.
7. **Duplicates** — source-range overlap, transcript shingles and (optionally) embeddings, against
   this video's clips and previously generated Shorts.
8. **Final ranking** — weighted combination (weights adjustable by the learning engine).

Scores are a ranking heuristic, not a virality prediction.

## Rendering

`EditTimeline` (non-destructive JSON, versioned per Short) is the single source of truth. The renderer
streams CFR frames from FFmpeg (NVDEC when available) into a Python compositor that applies the
virtual camera with sub-pixel affine warps (crop / split / fit-with-blur layouts, eased punch-ins,
B-roll), then streams into an FFmpeg encoder that burns in libass captions, applies optional
enhancement filters, assembles the audio segments with click-free joins, masters loudness with a
two-pass `loudnorm`, and encodes with NVENC (falling back to x264).

Captions are laid out by ShortForge itself using the real font metrics (libass size semantics), so every
word gets an exact position: highlighting and pop animations never reflow the line, and overflow /
safe-area violations are detected before rendering.

## GPU / memory strategy (8 GB laptops)

`ModelLifecycleManager` keeps at most one heavy model resident: Whisper is loaded, used and unloaded
before the LLM runs, and so on. CUDA out-of-memory triggers a recovery ladder
(fp16 → int8_float16 with a smaller batch → sequential decoding → CPU), with a notification.

## Security

* Loopback-only API; mutating requests require a custom header (blocks drive-by browser requests via
  CORS preflight).
* Secrets (API key, OAuth client, tokens) in Windows Credential Manager via `keyring`; never in the DB,
  files or logs (log records are redacted).
* No secrets in the repository; `.gitignore` covers env files, tokens and credentials.
