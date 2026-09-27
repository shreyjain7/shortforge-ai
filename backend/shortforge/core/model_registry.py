"""Catalogue of installable local models (sizes are shown before anything is downloaded)."""

from __future__ import annotations

import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

from shortforge.core.errors import DependencyMissing, RetryableError
from shortforge.core.logging import get_logger

log = get_logger("models")


@dataclass(frozen=True)
class ModelSpec:
    id: str
    kind: str  # whisper | llm | vision | embedding
    name: str
    purpose: str
    size_mb: int
    vram_mb: int
    source: str  # huggingface repo, ollama tag or URL
    recommended_for: tuple[str, ...] = ()
    filename: str | None = None
    sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["recommended_for"] = list(self.recommended_for)
        return d


WHISPER_MODELS: list[ModelSpec] = [
    ModelSpec("whisper:tiny", "whisper", "Whisper tiny", "Fast draft transcription", 75, 400,
              "Systran/faster-whisper-tiny"),
    ModelSpec("whisper:base", "whisper", "Whisper base", "Low-end CPUs", 145, 500,
              "Systran/faster-whisper-base", ("LOW",)),
    ModelSpec("whisper:small", "whisper", "Whisper small", "Good accuracy on modest GPUs/CPUs", 484, 1000,
              "Systran/faster-whisper-small", ("LOW",)),
    ModelSpec("whisper:medium", "whisper", "Whisper medium", "Higher accuracy, slower", 1530, 2600,
              "Systran/faster-whisper-medium"),
    ModelSpec("whisper:large-v3-turbo", "whisper", "Whisper large-v3 turbo",
              "Near large-v3 accuracy at ~5x speed; best choice for 8 GB GPUs", 1620, 2500,
              "mobiuslabsgmbh/faster-whisper-large-v3-turbo", ("BALANCED",)),
    ModelSpec("whisper:distil-large-v3", "whisper", "Distil-Whisper large-v3", "Fast English transcription",
              1510, 2400, "Systran/faster-distil-whisper-large-v3"),
    ModelSpec("whisper:large-v3", "whisper", "Whisper large-v3", "Maximum accuracy", 3090, 4500,
              "Systran/faster-whisper-large-v3", ("QUALITY",)),
]

LLM_MODELS: list[ModelSpec] = [
    ModelSpec("llm:qwen2.5:7b", "llm", "Qwen 2.5 7B", "Clip ranking, titles, metadata (recommended)", 4700, 5500,
              "qwen2.5:7b", ("BALANCED", "QUALITY")),
    ModelSpec("llm:qwen2.5:3b", "llm", "Qwen 2.5 3B", "Lightweight ranking for low VRAM", 1900, 2800,
              "qwen2.5:3b", ("LOW",)),
    ModelSpec("llm:qwen2.5:14b", "llm", "Qwen 2.5 14B", "Best judgement; needs 12+ GB VRAM", 9000, 10500,
              "qwen2.5:14b"),
    ModelSpec("llm:llama3.1:8b", "llm", "Llama 3.1 8B", "Alternative general model", 4900, 5800, "llama3.1:8b"),
    ModelSpec("llm:llama3.2:3b", "llm", "Llama 3.2 3B", "Small general model", 2000, 3000, "llama3.2:3b"),
    ModelSpec("llm:gemma3:4b", "llm", "Gemma 3 4B", "Small multilingual model", 3300, 4200, "gemma3:4b"),
    ModelSpec("llm:mistral:7b", "llm", "Mistral 7B", "Alternative 7B model", 4100, 5000, "mistral:7b"),
]

VISION_MODELS: list[ModelSpec] = [
    ModelSpec("vision:yunet", "vision", "YuNet face detector", "Fast face detection for smart reframing", 1, 50,
              "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
              "face_detection_yunet_2023mar.onnx", ("LOW", "BALANCED", "QUALITY"),
              filename="face_detection_yunet_2023mar.onnx"),
]

EMBEDDING_MODELS: list[ModelSpec] = [
    ModelSpec("embedding:nomic-embed-text", "embedding", "Nomic Embed Text",
              "Semantic duplicate detection between clips", 274, 600, "nomic-embed-text",
              ("BALANCED", "QUALITY")),
]

ALL_MODELS: dict[str, ModelSpec] = {m.id: m for m in WHISPER_MODELS + LLM_MODELS + VISION_MODELS + EMBEDDING_MODELS}


def whisper_dir(models_root: Path, name: str) -> Path:
    return models_root / "whisper" / name


def whisper_installed(models_root: Path, name: str) -> bool:
    d = whisper_dir(models_root, name)
    return (d / "model.bin").exists() and (d / "config.json").exists()


def vision_path(models_root: Path, spec: ModelSpec) -> Path:
    return models_root / "vision" / (spec.filename or spec.id.split(":", 1)[1])


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


ProgressFn = Callable[[float, str], None]


class ModelStore:
    """Install/remove/list models. LLM and embedding models are managed through Ollama."""

    def __init__(self, models_root: Path, ollama_url: str = "http://127.0.0.1:11434") -> None:
        self.root = models_root
        self.ollama_url = ollama_url.rstrip("/")

    # ---------------------------------------------------------------- listing
    def ollama_models(self) -> dict[str, dict[str, Any]] | None:
        try:
            resp = httpx.get(f"{self.ollama_url}/api/tags", timeout=3)
            resp.raise_for_status()
        except httpx.HTTPError:
            return None
        return {m["name"]: m for m in resp.json().get("models", [])}

    def status(self) -> list[dict[str, Any]]:
        ollama = self.ollama_models()
        out: list[dict[str, Any]] = []
        for spec in ALL_MODELS.values():
            info = spec.to_dict()
            if spec.kind == "whisper":
                name = spec.id.split(":", 1)[1]
                path = whisper_dir(self.root, name)
                info.update(installed=whisper_installed(self.root, name), location=str(path),
                            installed_size_mb=round(_dir_size(path) / 1024 / 1024), available=True)
            elif spec.kind == "vision":
                path = vision_path(self.root, spec)
                info.update(installed=path.exists(), location=str(path),
                            installed_size_mb=round(path.stat().st_size / 1024 / 1024, 2) if path.exists() else 0,
                            available=True)
            else:
                tag = spec.source
                present = ollama is not None and (tag in ollama or f"{tag}:latest" in ollama)
                size = (ollama or {}).get(tag, (ollama or {}).get(f"{tag}:latest", {})).get("size")
                info.update(installed=present, location="Ollama", available=ollama is not None,
                            installed_size_mb=round(size / 1024 / 1024) if size else 0)
            out.append(info)
        # Any extra Ollama models the user pulled themselves are usable too.
        if ollama:
            known = {m.source for m in ALL_MODELS.values() if m.kind in ("llm", "embedding")}
            for name, m in ollama.items():
                base = name[:-7] if name.endswith(":latest") else name
                if base in known or name in known:
                    continue
                out.append({"id": f"llm:{name}", "kind": "embedding" if "embed" in name else "llm", "name": name,
                            "purpose": "User-installed Ollama model", "size_mb": round(m.get("size", 0) / 1024 / 1024),
                            "vram_mb": round(m.get("size", 0) / 1024 / 1024 * 1.15), "source": name,
                            "recommended_for": [], "installed": True, "location": "Ollama", "available": True,
                            "installed_size_mb": round(m.get("size", 0) / 1024 / 1024)})
        return out

    def is_installed(self, model_id: str) -> bool:
        return any(m["id"] == model_id and m["installed"] for m in self.status())

    # ---------------------------------------------------------------- install
    def install(self, model_id: str, progress: ProgressFn | None = None,
                cancelled: Callable[[], bool] | None = None) -> None:
        spec = ALL_MODELS.get(model_id)
        if spec is None:
            if model_id.startswith("llm:"):
                self._ollama_pull(model_id.split(":", 1)[1], progress, cancelled)
                return
            raise ValueError(f"Unknown model {model_id}")
        if spec.kind == "whisper":
            self._install_whisper(spec, progress, cancelled)
        elif spec.kind == "vision":
            self._install_file(spec, progress)
        else:
            self._ollama_pull(spec.source, progress, cancelled)

    def _install_whisper(self, spec: ModelSpec, progress: ProgressFn | None,
                         cancelled: Callable[[], bool] | None) -> None:
        from huggingface_hub import snapshot_download

        name = spec.id.split(":", 1)[1]
        target = whisper_dir(self.root, name)
        target.mkdir(parents=True, exist_ok=True)
        done = threading.Event()
        error: list[BaseException] = []

        def run() -> None:
            try:
                snapshot_download(spec.source, local_dir=str(target),
                                  allow_patterns=["config.json", "preprocessor_config.json", "model.bin",
                                                  "tokenizer.json", "vocabulary.*"])
            except BaseException as exc:
                error.append(exc)
            finally:
                done.set()

        t = threading.Thread(target=run, daemon=True)
        t.start()
        expected = spec.size_mb * 1024 * 1024
        while not done.wait(0.5):
            if progress:
                # hf_hub writes to a .cache/huggingface/download folder inside local_dir
                size = _dir_size(target)
                progress(min(0.99, size / expected), f"Downloading {spec.name}")
            if cancelled and cancelled():
                raise RetryableError("Model download cancelled")
        if error:
            raise RetryableError(f"Could not download {spec.name}.", detail=str(error[0])) from error[0]
        if not whisper_installed(self.root, name):
            raise RetryableError(f"{spec.name} download incomplete.")
        if progress:
            progress(1.0, f"{spec.name} installed")

    def _install_file(self, spec: ModelSpec, progress: ProgressFn | None) -> None:
        target = vision_path(self.root, spec)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".part")
        with httpx.stream("GET", spec.source, follow_redirects=True, timeout=60) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length") or 0)
            done = 0
            with tmp.open("wb") as fh:
                for chunk in resp.iter_bytes(64 * 1024):
                    fh.write(chunk)
                    done += len(chunk)
                    if progress and total:
                        progress(done / total, f"Downloading {spec.name}")
        tmp.replace(target)
        if progress:
            progress(1.0, f"{spec.name} installed")

    def _ollama_pull(self, tag: str, progress: ProgressFn | None, cancelled: Callable[[], bool] | None) -> None:
        if self.ollama_models() is None:
            raise DependencyMissing("Ollama is not running. Install it from ollama.com and start it.")
        last = 0.0
        with httpx.stream("POST", f"{self.ollama_url}/api/pull", json={"model": tag, "stream": True},
                          timeout=httpx.Timeout(30, read=600)) as resp:
            resp.raise_for_status()
            import json as _json

            for line in resp.iter_lines():
                if cancelled and cancelled():
                    raise RetryableError("Model download cancelled")
                if not line:
                    continue
                msg = _json.loads(line)
                if msg.get("error"):
                    raise RetryableError(f"Ollama could not pull {tag}: {msg['error']}")
                total, completed = msg.get("total"), msg.get("completed")
                if progress and total and completed and time.monotonic() - last > 0.3:
                    last = time.monotonic()
                    progress(completed / total, f"{msg.get('status', 'pulling')} {tag}")
        if progress:
            progress(1.0, f"{tag} installed")

    # ---------------------------------------------------------------- remove
    def remove(self, model_id: str) -> None:
        spec = ALL_MODELS.get(model_id)
        kind = spec.kind if spec else ("llm" if model_id.startswith("llm:") else "")
        if kind == "whisper":
            shutil.rmtree(whisper_dir(self.root, model_id.split(":", 1)[1]), ignore_errors=True)
        elif kind == "vision" and spec:
            vision_path(self.root, spec).unlink(missing_ok=True)
        elif kind in ("llm", "embedding"):
            tag = spec.source if spec else model_id.split(":", 1)[1]
            resp = httpx.request("DELETE", f"{self.ollama_url}/api/delete", json={"model": tag}, timeout=30)
            if resp.status_code not in (200, 404):
                raise RetryableError(f"Ollama could not remove {tag}: {resp.text[:200]}")
        else:
            raise ValueError(f"Unknown model {model_id}")
