"""Ollama and llama.cpp (llama-server) providers."""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from shortforge.core.config import LLMSettings
from shortforge.core.errors import RetryableError
from shortforge.core.logging import get_logger
from shortforge.engines.llm.base import LLMProvider, LLMUnavailable

log = get_logger("llm")

# Preference order when the model is "auto" (first installed wins).
AUTO_PREFERENCE = [
    "qwen2.5:14b", "qwen2.5:7b", "qwen3:8b", "llama3.1:8b", "gemma3:12b", "mistral:7b", "gemma3:4b", "qwen3:4b",
    "qwen2.5:3b", "llama3.2:3b",
]
_NON_CHAT_HINTS = ("embed", "-vl", "vision", "llava", "moondream", "clip", "whisper")


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434", temperature: float = 0.2,
                 timeout_s: float = 180.0, keep_alive: str = "10m") -> None:
        super().__init__(model, temperature, timeout_s)
        self.base_url = base_url.rstrip("/")
        self.keep_alive = keep_alive
        self._client = httpx.Client(timeout=httpx.Timeout(timeout_s, connect=5))

    def is_available(self) -> bool:
        try:
            return self._client.get(f"{self.base_url}/api/tags", timeout=3).status_code == 200
        except httpx.HTTPError:
            return False

    def list_models(self) -> list[str]:
        try:
            resp = self._client.get(f"{self.base_url}/api/tags", timeout=5)
            resp.raise_for_status()
        except httpx.HTTPError:
            return []
        return [m["name"] for m in resp.json().get("models", [])]

    def chat(self, system: str, user: str, *, schema: dict[str, Any] | None = None, max_tokens: int = 800,
             temperature: float | None = None) -> str:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {"temperature": self.temperature if temperature is None else temperature,
                        "num_predict": max_tokens, "num_ctx": 8192},
        }
        if schema is not None:
            body["format"] = schema
        if self.model.startswith("qwen3"):
            body["think"] = False
        try:
            resp = self._client.post(f"{self.base_url}/api/chat", json=body)
        except httpx.ConnectError as exc:
            raise LLMUnavailable("Ollama is not running.") from exc
        except httpx.HTTPError as exc:
            raise RetryableError("Local LLM request failed.", detail=str(exc)) from exc
        if resp.status_code == 404:
            raise LLMUnavailable(f"Ollama model '{self.model}' is not installed.")
        if resp.status_code >= 500:
            text = resp.text.lower()
            if "memory" in text or "cuda" in text:
                raise RetryableError("The LLM ran out of GPU memory.", detail=resp.text[:300])
            raise RetryableError(f"Ollama error {resp.status_code}", detail=resp.text[:300])
        resp.raise_for_status()
        return resp.json().get("message", {}).get("content", "")

    def embed(self, texts: list[str], model: str | None = None) -> list[list[float]] | None:
        model = model or "nomic-embed-text"
        try:
            resp = self._client.post(f"{self.base_url}/api/embed", json={"model": model, "input": texts,
                                                                          "keep_alive": "2m"})
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None
        return resp.json().get("embeddings")

    def load(self) -> None:
        try:
            self._client.post(f"{self.base_url}/api/generate", json={"model": self.model, "prompt": "",
                                                                     "keep_alive": self.keep_alive}, timeout=120)
        except httpx.HTTPError as exc:
            log.debug("ollama warmup failed: %s", exc)

    def unload(self) -> None:
        with contextlib.suppress(httpx.HTTPError):
            self._client.post(f"{self.base_url}/api/generate", json={"model": self.model, "keep_alive": 0},
                              timeout=30)


class LlamaCppProvider(LLMProvider):
    """llama.cpp's ``llama-server`` (OpenAI-compatible HTTP API)."""

    name = "llamacpp"

    def __init__(self, model: str, base_url: str = "http://127.0.0.1:8080", temperature: float = 0.2,
                 timeout_s: float = 180.0) -> None:
        super().__init__(model, temperature, timeout_s)
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=httpx.Timeout(timeout_s, connect=5))

    def is_available(self) -> bool:
        try:
            return self._client.get(f"{self.base_url}/health", timeout=3).status_code == 200
        except httpx.HTTPError:
            return False

    def list_models(self) -> list[str]:
        try:
            resp = self._client.get(f"{self.base_url}/v1/models", timeout=5)
            resp.raise_for_status()
            return [m["id"] for m in resp.json().get("data", [])]
        except httpx.HTTPError:
            return []

    def chat(self, system: str, user: str, *, schema: dict[str, Any] | None = None, max_tokens: int = 800,
             temperature: float | None = None) -> str:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": max_tokens,
        }
        if schema is not None:
            body["response_format"] = {"type": "json_object", "schema": schema}
        try:
            resp = self._client.post(f"{self.base_url}/v1/chat/completions", json=body)
        except httpx.ConnectError as exc:
            raise LLMUnavailable("llama-server is not running.") from exc
        except httpx.HTTPError as exc:
            raise RetryableError("Local LLM request failed.", detail=str(exc)) from exc
        if resp.status_code >= 500:
            raise RetryableError(f"llama-server error {resp.status_code}", detail=resp.text[:300])
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    def embed(self, texts: list[str], model: str | None = None) -> list[list[float]] | None:
        try:
            resp = self._client.post(f"{self.base_url}/v1/embeddings", json={"input": texts, "model": self.model})
            if resp.status_code != 200:
                return None
            return [d["embedding"] for d in resp.json()["data"]]
        except (httpx.HTTPError, KeyError):
            return None


def find_ollama_binary() -> str | None:
    found = shutil.which("ollama")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidate = Path(local) / "Programs" / "Ollama" / "ollama.exe"
        if candidate.exists():
            return str(candidate)
    return None


_start_lock = threading.Lock()


def ensure_ollama_running(base_url: str, wait_s: float = 15.0) -> bool:
    """Start ``ollama serve`` in the background if Ollama is installed but not running."""
    probe = OllamaProvider("", base_url)
    if probe.is_available():
        return True
    if urlparse(base_url).hostname not in ("127.0.0.1", "localhost"):
        return False
    binary = find_ollama_binary()
    if not binary:
        return False
    with _start_lock:
        if probe.is_available():
            return True
        log.info("starting Ollama server (%s)", binary)
        flags = 0x08000000 | 0x00000200 if os.name == "nt" else 0  # CREATE_NO_WINDOW | NEW_PROCESS_GROUP
        try:
            subprocess.Popen([binary, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True)
        except OSError as exc:
            log.warning("could not start Ollama: %s", exc)
            return False
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            if probe.is_available():
                return True
            time.sleep(0.5)
    return False


def pick_ollama_model(installed: list[str], preferred: str | None) -> str | None:
    chat_models = [m for m in installed if not any(h in m for h in _NON_CHAT_HINTS)]
    norm = {m[:-7] if m.endswith(":latest") else m: m for m in chat_models}
    if preferred and preferred in norm:
        return norm[preferred]
    for cand in AUTO_PREFERENCE:
        if cand in norm:
            return norm[cand]
    return chat_models[0] if chat_models else None


def create_provider(settings: LLMSettings, recommended: str | None = None) -> LLMProvider | None:
    """Build the configured provider, or None when no local LLM is configured/available."""
    if settings.provider == "none":
        return None
    if settings.provider == "llamacpp":
        provider: LLMProvider = LlamaCppProvider(settings.model if settings.model != "auto" else "local",
                                                 settings.llamacpp_url, settings.temperature, settings.timeout_s)
        return provider if provider.is_available() else None
    ollama = OllamaProvider("", settings.ollama_url, settings.temperature, settings.timeout_s)
    if not ensure_ollama_running(settings.ollama_url):
        return None
    installed = ollama.list_models()
    if settings.model != "auto":
        model = settings.model if (settings.model in installed or f"{settings.model}:latest" in installed) else None
    else:
        model = pick_ollama_model(installed, recommended)
    if not model:
        return None
    ollama.model = model
    return ollama
