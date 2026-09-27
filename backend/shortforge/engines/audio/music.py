"""Local music library analysis: tempo, beats, energy and duration (numpy only, no extra deps).

Tempo is estimated from the autocorrelation of a spectral-flux onset envelope; beats are placed by
dynamic-programming beat tracking (Ellis 2007) constrained to that tempo.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from shortforge.core.logging import get_logger
from shortforge.engines.media import ffmpeg as ff

log = get_logger("music")

SR = 22050
HOP = 512
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}


@dataclass
class TrackInfo:
    path: str
    duration: float
    tempo: float
    beats: list[float] = field(default_factory=list)
    energy: float = 0.0  # 0..1 (relative loudness/activity)
    mtime: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def _decode(path: Path, max_seconds: float = 240.0) -> np.ndarray:
    cmd = [ff.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin", "-t", str(max_seconds), "-i", str(path),
           "-ac", "1", "-ar", str(SR), "-f", "f32le", "pipe:1"]
    out = subprocess.run(cmd, capture_output=True, timeout=180, creationflags=ff.CREATE_NO_WINDOW).stdout
    return np.frombuffer(out, dtype=np.float32)


def onset_envelope(y: np.ndarray) -> np.ndarray:
    n_fft = 2048
    if len(y) < n_fft:
        return np.zeros(1, dtype=np.float32)
    frames = 1 + (len(y) - n_fft) // HOP
    window = np.hanning(n_fft).astype(np.float32)
    idx = np.arange(n_fft)[None, :] + HOP * np.arange(frames)[:, None]
    spec = np.abs(np.fft.rfft(y[idx] * window, axis=1))
    logspec = np.log1p(10 * spec)
    flux = np.maximum(0.0, np.diff(logspec, axis=0)).sum(axis=1)
    flux = np.concatenate([[0.0], flux])
    flux -= np.convolve(flux, np.ones(16) / 16, mode="same")  # remove slow trend
    flux = np.maximum(flux, 0)
    return (flux / (flux.max() + 1e-9)).astype(np.float32)


def estimate_tempo(env: np.ndarray, lo_bpm: float = 70, hi_bpm: float = 180) -> float:
    fps = SR / HOP
    if len(env) < fps * 4:
        return 120.0
    ac = np.correlate(env - env.mean(), env - env.mean(), mode="full")[len(env) - 1 :]
    lags = np.arange(len(ac))
    min_lag, max_lag = int(fps * 60 / hi_bpm), int(fps * 60 / lo_bpm)
    window = ac[min_lag : max_lag + 1]
    if not len(window):
        return 120.0
    # Mild preference for tempi around 120 BPM (log-Gaussian prior) to avoid octave errors.
    bpm = 60 * fps / lags[min_lag : max_lag + 1]
    prior = np.exp(-0.5 * (np.log2(bpm / 120.0) / 0.9) ** 2)
    best = int(np.argmax(window * prior))
    return float(round(bpm[best], 2))


def track_beats(env: np.ndarray, tempo: float, tightness: float = 100.0) -> list[float]:
    fps = SR / HOP
    period = 60.0 * fps / tempo
    n = len(env)
    if n < 2:
        return []
    score = env.astype(np.float64).copy()
    backlink = -np.ones(n, dtype=np.int64)
    lo, hi = round(period / 2), round(period * 2)
    for i in range(hi, n):
        prev = np.arange(i - hi, i - lo + 1)
        prev = prev[prev >= 0]
        if not len(prev):
            continue
        penalty = -tightness * (np.log((i - prev) / period)) ** 2
        cand = score[prev] + penalty
        k = int(np.argmax(cand))
        score[i] = env[i] + cand[k]
        backlink[i] = prev[k]
    # Start from the best-scoring frame in the last period and walk back.
    tail = max(0, n - int(period))
    i = tail + int(np.argmax(score[tail:]))
    beats = []
    while i >= 0:
        beats.append(i)
        i = int(backlink[i])
    return [round(b / fps, 3) for b in reversed(beats)]


def analyze_track(path: Path) -> TrackInfo:
    info = ff.probe(path)
    y = _decode(path)
    env = onset_envelope(y)
    tempo = estimate_tempo(env)
    beats = track_beats(env, tempo)
    rms = float(np.sqrt(np.mean(y**2))) if len(y) else 0.0
    energy = float(np.clip((20 * np.log10(rms + 1e-9) + 35) / 25, 0, 1)) * 0.6 + float(np.clip(env.mean() * 6, 0, 1)) * 0.4
    return TrackInfo(str(path), info.duration, tempo, beats, round(energy, 3), path.stat().st_mtime)


def index_library(folder: Path, cache_file: Path) -> list[TrackInfo]:
    """Analyse every audio file in ``folder`` (cached by path + mtime)."""
    cache: dict[str, dict] = {}
    if cache_file.exists():
        try:
            cache = json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cache = {}
    tracks: list[TrackInfo] = []
    changed = False
    for p in sorted(folder.rglob("*")):
        if p.suffix.lower() not in AUDIO_EXT or not p.is_file():
            continue
        entry = cache.get(str(p))
        if entry and abs(entry.get("mtime", 0) - p.stat().st_mtime) < 1:
            tracks.append(TrackInfo(**entry))
            continue
        try:
            t = analyze_track(p)
        except Exception as exc:
            log.warning("could not analyse %s: %s", p.name, exc)
            continue
        cache[str(p)] = t.to_dict()
        tracks.append(t)
        changed = True
    if changed:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(cache), encoding="utf-8")
    return tracks


def pick_track(tracks: list[TrackInfo], clip_duration: float, clip_energy: float = 0.5,
               seed: int = 0) -> TrackInfo | None:
    """Prefer tracks long enough for the clip whose energy matches the clip's delivery."""
    if not tracks:
        return None
    rng = np.random.default_rng(seed)

    def score(t: TrackInfo) -> float:
        fits = 1.0 if t.duration >= clip_duration + 2 else 0.4
        return fits * (1 - abs(t.energy - clip_energy)) + rng.uniform(0, 0.15)

    return max(tracks, key=score)


def snap_to_beats(times: list[float], beats: list[float], offset: float, window: float = 0.25) -> list[float]:
    """Move edit times (output timeline) onto the nearest music beat if one is within ``window`` s."""
    if not beats:
        return times
    arr = np.asarray(beats) - offset
    out = []
    for t in times:
        k = int(np.argmin(np.abs(arr - t)))
        out.append(round(float(arr[k]), 3) if abs(arr[k] - t) <= window and arr[k] >= 0 else t)
    return out
