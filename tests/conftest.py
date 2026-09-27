from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def pytest_configure(config) -> None:  # type: ignore[no-untyped-def]
    (ROOT / "build").mkdir(exist_ok=True)


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated ShortForge data directory + fresh database for each test."""
    from shortforge.core import paths, secrets, settings_store
    from shortforge.database.session import init_db

    monkeypatch.setenv("SHORTFORGE_DATA_DIR", str(tmp_path / "data"))
    paths._paths = None
    p = paths.get_paths()
    init_db(p.database)
    secrets.configure_fallback(p.root / ".secrets.json", force=True)
    settings_store.invalidate_cache()
    yield p.root
    settings_store.invalidate_cache()
    paths._paths = None


@pytest.fixture()
def words_factory():
    from shortforge.engines.transcription.types import Word

    def make(text: str, start: float = 0.0, word_dur: float = 0.3, gap: float = 0.05,
             pauses: dict[int, float] | None = None) -> list[Word]:
        out, t = [], start
        for i, tok in enumerate(text.split()):
            t += (pauses or {}).get(i, 0.0)
            out.append(Word(i, " " + tok, round(t, 3), round(t + word_dur, 3), 0.95, 0))
            t += word_dur + gap
        return out

    return make


def has_ffmpeg() -> bool:
    from shortforge.engines.media.ffmpeg import find_ffmpeg

    return find_ffmpeg() is not None


requires_ffmpeg = pytest.mark.skipif(not has_ffmpeg(), reason="FFmpeg not installed")
os.environ.setdefault("SHORTFORGE_LOG_LEVEL", "WARNING")
