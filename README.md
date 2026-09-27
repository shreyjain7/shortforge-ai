# ShortForge AI

Local, privacy-first, zero-subscription studio that turns long-form videos from **any public YouTube
channel** (or local files) into polished vertical Shorts: discovery → download → transcription →
multi-pass clip finding → smart 9:16 reframing → animated captions → mastering → render → QC →
metadata → scheduling/upload → analytics → learning.

Everything runs on one Windows laptop. No paid AI APIs, no Redis/Docker, no cloud GPUs.

> Work in progress — see `docs/STATUS.md` for exactly what is implemented and verified.

## Stack

| Layer | Technology |
| --- | --- |
| Desktop shell | Tauri 2 (Rust) |
| UI | React + TypeScript + Vite + Framer Motion |
| Backend | Python 3.12, FastAPI, SQLAlchemy + Alembic (SQLite, WAL), asyncio persistent job queue |
| Discovery / download | yt-dlp (+ Node JS runtime), YouTube Data API v3 (optional key), channel Atom feeds |
| Transcription | faster-whisper (CTranslate2, CUDA fp16/int8), Silero VAD (ONNX) |
| Understanding | Ollama or llama.cpp (`llama-server`) — Qwen / Llama / Gemma / Mistral … |
| Vision | OpenCV YuNet face detection, IoU tracking, mouth-motion speaker activity, PySceneDetect |
| Rendering | FFmpeg (NVDEC/NVENC), libass captions, two-pass loudnorm |

## Quick start (Windows)

```powershell
winget install Gyan.FFmpeg astral-sh.uv Ollama.Ollama
uv venv --python 3.12 .venv
uv pip install --python .venv -e ".[cuda,dev]"
.venv\Scripts\shortforge install-model whisper:large-v3-turbo
.venv\Scripts\shortforge install-model vision:yunet
ollama pull qwen2.5:7b
.venv\Scripts\shortforge-server            # API on http://127.0.0.1:8756
```

Headless end-to-end run on any public video / @handle / channel / playlist / local file:

```powershell
.venv\Scripts\shortforge run "https://www.youtube.com/watch?v=VIDEO_ID" --shorts 2
```

## Tests

```powershell
.venv\Scripts\python -m pytest          # unit + integration (YouTube mocked; real FFmpeg render)
.venv\Scripts\ruff check backend tests
```

## Security

Secrets (YouTube API key, OAuth client and tokens) live in Windows Credential Manager via `keyring`,
never in the database, files in the repo, or logs (log output is redacted). The local API only
listens on loopback and rejects mutating requests without the app's client header.

## License

MIT. Bundled caption fonts are licensed under the SIL Open Font License (see `assets/fonts/OFL-*.txt`).
