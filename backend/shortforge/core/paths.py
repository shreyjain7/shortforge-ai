"""Filesystem layout for ShortForge data.

The data root is resolved (in priority order) from:
1. the ``SHORTFORGE_DATA_DIR`` environment variable,
2. the bootstrap file (``%APPDATA%/ShortForge/bootstrap.json``) written by first-run setup,
3. ``~/ShortForgeData``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

SUBDIRS = (
    "Sources",
    "Cache",
    "Models",
    "Projects",
    "Proxies",
    "Renders",
    "Thumbnails",
    "Captions",
    "Analytics",
    "Temp",
    "Logs",
)


def _config_home() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / ".config"
    return base / "ShortForge"


def bootstrap_file() -> Path:
    return _config_home() / "bootstrap.json"


def read_bootstrap() -> dict:
    path = bootstrap_file()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def write_bootstrap(data: dict) -> None:
    path = bootstrap_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = {**read_bootstrap(), **data}
    path.write_text(json.dumps(merged, indent=2), encoding="utf-8")


def resolve_data_root() -> Path:
    env = os.environ.get("SHORTFORGE_DATA_DIR")
    if env:
        return Path(env).expanduser().resolve()
    boot = read_bootstrap().get("data_dir")
    if boot:
        return Path(boot).expanduser().resolve()
    return (Path.home() / "ShortForgeData").resolve()


@dataclass(frozen=True)
class DataPaths:
    root: Path

    @property
    def sources(self) -> Path:
        return self.root / "Sources"

    @property
    def cache(self) -> Path:
        return self.root / "Cache"

    @property
    def models(self) -> Path:
        return self.root / "Models"

    @property
    def projects(self) -> Path:
        return self.root / "Projects"

    @property
    def proxies(self) -> Path:
        return self.root / "Proxies"

    @property
    def renders(self) -> Path:
        return self.root / "Renders"

    @property
    def thumbnails(self) -> Path:
        return self.root / "Thumbnails"

    @property
    def captions(self) -> Path:
        return self.root / "Captions"

    @property
    def analytics(self) -> Path:
        return self.root / "Analytics"

    @property
    def temp(self) -> Path:
        return self.root / "Temp"

    @property
    def logs(self) -> Path:
        return self.root / "Logs"

    @property
    def database(self) -> Path:
        return self.root / "shortforge.db"

    def video_cache(self, video_id: int) -> Path:
        path = self.cache / f"video_{video_id}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def ensure(self) -> DataPaths:
        self.root.mkdir(parents=True, exist_ok=True)
        for name in SUBDIRS:
            (self.root / name).mkdir(parents=True, exist_ok=True)
        return self


_paths: DataPaths | None = None


def get_paths() -> DataPaths:
    global _paths
    if _paths is None:
        _paths = DataPaths(resolve_data_root()).ensure()
    return _paths


def set_data_root(root: Path) -> DataPaths:
    """Override the data root (used by tests and first-run storage selection)."""
    global _paths
    _paths = DataPaths(Path(root).resolve()).ensure()
    return _paths


def project_root() -> Path:
    """Repository root (contains presets/ and assets/)."""
    return Path(__file__).resolve().parents[3]


def resources_dir() -> Path:
    """Bundled data shipped inside the package (fonts, caption presets)."""
    return Path(__file__).resolve().parents[1] / "resources"


def assets_dir() -> Path:
    return resources_dir()


def presets_dir() -> Path:
    return resources_dir()
