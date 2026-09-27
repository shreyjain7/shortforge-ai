"""Local LLM provider abstraction."""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any

from shortforge.core.errors import ShortForgeError


class LLMUnavailable(ShortForgeError):
    pass


class LLMProvider(ABC):
    name = "base"

    def __init__(self, model: str, temperature: float = 0.2, timeout_s: float = 180.0) -> None:
        self.model = model
        self.temperature = temperature
        self.timeout_s = timeout_s

    @abstractmethod
    def is_available(self) -> bool: ...

    @abstractmethod
    def list_models(self) -> list[str]: ...

    @abstractmethod
    def chat(self, system: str, user: str, *, schema: dict[str, Any] | None = None, max_tokens: int = 800,
             temperature: float | None = None) -> str: ...

    def embed(self, texts: list[str], model: str | None = None) -> list[list[float]] | None:
        return None

    def load(self) -> None:  # noqa: B027 - optional hook, default no-op
        """Warm the model (optional)."""

    def unload(self) -> None:  # noqa: B027 - optional hook, default no-op
        """Release VRAM held by the model (optional)."""

    def chat_json(self, system: str, user: str, *, schema: dict[str, Any], max_tokens: int = 800,
                  retries: int = 2) -> dict[str, Any]:
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            raw = self.chat(system, user, schema=schema, max_tokens=max_tokens,
                            temperature=self.temperature if attempt == 0 else min(0.7, self.temperature + 0.2))
            try:
                return parse_json_object(raw)
            except ValueError as exc:
                last_err = exc
        raise ShortForgeError("The local LLM returned malformed JSON.", detail=str(last_err))


def parse_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    # qwen3-style reasoning blocks
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise ValueError(f"no JSON object in: {text[:200]}") from None
        obj = json.loads(m.group(0))
    if not isinstance(obj, dict):
        raise ValueError("JSON is not an object")
    return obj
