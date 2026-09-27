"""Audio analysis: speech activity (Silero VAD), energy envelope, silences and pacing plans.

Everything runs on the 16 kHz mono WAV extracted once per video, so the source is never
decoded again for audio work.
"""

from __future__ import annotations

import itertools
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from shortforge.core.logging import get_logger
from shortforge.engines.media import ffmpeg as ff

log = get_logger("audio")

SR = 16000
HOP_S = 0.05  # 20 Hz feature rate


def extract_audio(source: Path, out_wav: Path, *, progress=None, cancel=None, duration: float | None = None) -> Path:
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_wav.with_suffix(".tmp.wav")
    ff.run_ffmpeg(
        ["-i", str(source), "-vn", "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", "-f", "wav", str(tmp)],
        duration=duration, progress=progress, cancel=cancel, description="Extracting audio",
    )
    tmp.replace(out_wav)
    return out_wav


def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        if wf.getsampwidth() != 2:
            raise ValueError("expected 16-bit PCM wav")
        frames = wf.readframes(wf.getnframes())
        channels = wf.getnchannels()
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return audio


def energy_envelope(audio: np.ndarray, sr: int = SR, hop_s: float = HOP_S) -> np.ndarray:
    """RMS energy in dBFS per hop."""
    hop = int(sr * hop_s)
    n = len(audio) // hop
    if n == 0:
        return np.zeros(0, dtype=np.float32)
    frames = audio[: n * hop].reshape(n, hop)
    rms = np.sqrt(np.mean(frames**2, axis=1) + 1e-12)
    return (20 * np.log10(rms + 1e-9)).astype(np.float32)


def detect_speech(audio: np.ndarray, sr: int = SR) -> list[tuple[float, float]]:
    """Silero VAD (ONNX, bundled with faster-whisper) -> speech intervals in seconds."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    opts = VadOptions(threshold=0.5, min_speech_duration_ms=150, min_silence_duration_ms=250, speech_pad_ms=60)
    stamps = get_speech_timestamps(audio, opts, sampling_rate=sr)
    return [(s["start"] / sr, s["end"] / sr) for s in stamps]


def silences_from_speech(speech: list[tuple[float, float]], duration: float, min_len: float = 0.3) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    cursor = 0.0
    for s, e in speech:
        if s - cursor >= min_len:
            out.append((cursor, s))
        cursor = max(cursor, e)
    if duration - cursor >= min_len:
        out.append((cursor, duration))
    return out


@dataclass
class AudioFeatures:
    duration: float
    hop_s: float
    energy_db: np.ndarray
    speech: list[tuple[float, float]]
    silences: list[tuple[float, float]]
    loudness_ref_db: float = field(default=-30.0)

    def energy_at(self, start: float, end: float) -> float:
        a, b = int(start / self.hop_s), max(int(end / self.hop_s), int(start / self.hop_s) + 1)
        seg = self.energy_db[a:b]
        return float(seg.mean()) if len(seg) else -90.0

    def energy_variation(self, start: float, end: float) -> float:
        a, b = int(start / self.hop_s), int(end / self.hop_s)
        seg = self.energy_db[a:b]
        seg = seg[seg > -60]
        return float(seg.std()) if len(seg) > 4 else 0.0

    def speech_ratio(self, start: float, end: float) -> float:
        if end <= start:
            return 0.0
        total = 0.0
        for s, e in self.speech:
            if e <= start:
                continue
            if s >= end:
                break
            total += min(e, end) - max(s, start)
        return total / (end - start)

    def longest_silence(self, start: float, end: float) -> float:
        best = 0.0
        for s, e in self.silences:
            if e <= start or s >= end:
                continue
            best = max(best, min(e, end) - max(s, start))
        return best

    def to_npz(self, path: Path) -> None:
        np.savez_compressed(
            path, duration=self.duration, hop_s=self.hop_s, energy_db=self.energy_db,
            speech=np.array(self.speech, dtype=np.float32).reshape(-1, 2),
            silences=np.array(self.silences, dtype=np.float32).reshape(-1, 2),
            loudness_ref_db=self.loudness_ref_db,
        )

    @classmethod
    def from_npz(cls, path: Path) -> AudioFeatures:
        d = np.load(path)
        return cls(float(d["duration"]), float(d["hop_s"]), d["energy_db"],
                   [tuple(map(float, r)) for r in d["speech"]], [tuple(map(float, r)) for r in d["silences"]],
                   float(d["loudness_ref_db"]))

    def summary(self) -> dict[str, Any]:
        voiced = self.energy_db[self.energy_db > -60]
        return {
            "duration": round(self.duration, 2),
            "speech_ratio": round(self.speech_ratio(0, self.duration), 3),
            "silence_count": len(self.silences),
            "mean_energy_db": round(float(voiced.mean()), 2) if len(voiced) else None,
        }


def analyze_audio(wav_path: Path) -> AudioFeatures:
    audio = load_wav(wav_path)
    duration = len(audio) / SR
    energy = energy_envelope(audio)
    speech = detect_speech(audio)
    silences = silences_from_speech(speech, duration)
    voiced = energy[energy > -60]
    ref = float(np.percentile(voiced, 60)) if len(voiced) else -30.0
    log.info("audio analysis: %.0fs, %d speech regions, %d silences", duration, len(speech), len(silences))
    return AudioFeatures(duration, HOP_S, energy, speech, silences, ref)


# ---------------------------------------------------------------------------- pacing

PACING = {
    # gap longer than `trigger` seconds is shortened to `keep` seconds
    "natural": {"trigger": 1.0, "keep": 0.40},
    "balanced": {"trigger": 0.65, "keep": 0.28},
    "aggressive": {"trigger": 0.40, "keep": 0.18},
}


def plan_silence_cuts(word_times: list[tuple[float, float]], start: float, end: float,
                      pacing: str = "balanced") -> list[tuple[float, float]]:
    """Return the source sub-ranges to keep inside [start, end].

    Cuts are placed only in gaps *between* words, never inside a word, and each shortened gap
    keeps ``keep`` seconds of breathing room split around the cut so speech never sounds chopped.
    """
    params = PACING.get(pacing, PACING["balanced"])
    trigger, keep = params["trigger"], params["keep"]
    words = [(s, e) for s, e in word_times if e > start and s < end]
    if not words:
        return [(start, end)]
    ranges: list[tuple[float, float]] = []
    cur = start
    for (_s1, e1), (s2, _e2) in itertools.pairwise(words):
        gap = s2 - e1
        if gap > trigger:
            cut_a = e1 + keep / 2
            cut_b = s2 - keep / 2
            if cut_b - cut_a > 0.05:
                ranges.append((cur, cut_a))
                cur = cut_b
    ranges.append((cur, end))
    return [(round(a, 3), round(b, 3)) for a, b in ranges if b - a > 0.05]
