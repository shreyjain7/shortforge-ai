"""Faster-Whisper transcription with word timestamps, GPU lifecycle management and OOM recovery."""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from shortforge.core.config import TranscriptionSettings
from shortforge.core.errors import DependencyMissing, GPUMemoryError, is_cuda_oom
from shortforge.core.gpu import models as model_manager
from shortforge.core.logging import get_logger
from shortforge.core.model_registry import ALL_MODELS, WHISPER_MODELS, whisper_dir, whisper_installed
from shortforge.engines.media.ffmpeg import CancelToken
from shortforge.engines.transcription.cuda_dlls import ensure_cuda_dlls
from shortforge.engines.transcription.sentences import build_sentences, sanitize_words
from shortforge.engines.transcription.types import Segment, TranscriptResult, Word

log = get_logger("transcription")

ProgressFn = Callable[[float, str], None]


@dataclass
class Attempt:
    device: str
    compute_type: str
    batch_size: int  # 0 = sequential (non-batched) decoding


def installed_whisper_models(models_root: Path) -> list[str]:
    return [m.id.split(":", 1)[1] for m in WHISPER_MODELS if whisper_installed(models_root, m.id.split(":", 1)[1])]


def choose_model(models_root: Path, settings: TranscriptionSettings, recommended: str) -> str:
    installed = installed_whisper_models(models_root)
    if settings.model != "auto":
        if settings.model not in installed:
            spec = ALL_MODELS.get(f"whisper:{settings.model}")
            size = f" ({spec.size_mb} MB)" if spec else ""
            raise DependencyMissing(f"Whisper model '{settings.model}'{size} is not installed. Install it on the Models page.")
        return settings.model
    if recommended in installed:
        return recommended
    # Otherwise the most capable installed model.
    preference = ["large-v3", "large-v3-turbo", "distil-large-v3", "medium", "small", "base", "tiny"]
    for name in preference:
        if name in installed:
            return name
    spec = ALL_MODELS.get(f"whisper:{recommended}")
    raise DependencyMissing(
        f"No Whisper model is installed. Install '{recommended}'"
        + (f" ({spec.size_mb} MB)" if spec else "") + " on the Models page."
    )


def attempt_ladder(device: str, compute_type: str, batch_size: int, cuda_ok: bool) -> list[Attempt]:
    """Progressively cheaper configurations tried after CUDA OOM."""
    attempts: list[Attempt] = []
    if device in ("auto", "cuda") and cuda_ok:
        ct = compute_type if compute_type != "auto" else "float16"
        attempts.append(Attempt("cuda", ct, max(1, batch_size)))
        if ct != "int8_float16":
            attempts.append(Attempt("cuda", "int8_float16", max(1, batch_size // 2)))
        attempts.append(Attempt("cuda", "int8_float16", 0))
    attempts.append(Attempt("cpu", "int8", 0))
    return attempts


class WhisperTranscriber:
    def __init__(self, models_root: Path, settings: TranscriptionSettings, *, recommended: str = "large-v3-turbo",
                 cuda_available: bool = True, allow_cpu_fallback: bool = True) -> None:
        self.models_root = models_root
        self.settings = settings
        self.recommended = recommended
        self.cuda_available = cuda_available
        self.allow_cpu_fallback = allow_cpu_fallback

    def transcribe(self, audio_path: Path, *, duration: float | None = None, progress: ProgressFn | None = None,
                   cancel: CancelToken | None = None) -> TranscriptResult:
        ensure_cuda_dlls()
        model_name = choose_model(self.models_root, self.settings, self.recommended)
        ladder = attempt_ladder(self.settings.device, self.settings.compute_type, self.settings.batch_size,
                                self.cuda_available)
        if self.settings.device == "cpu":
            ladder = [Attempt("cpu", "int8", 0)]
        elif not self.allow_cpu_fallback and len(ladder) > 1:
            ladder = [a for a in ladder if a.device != "cpu"] or ladder
        last_exc: Exception | None = None
        for attempt in ladder:
            try:
                with model_manager.gpu_session("whisper"):
                    return self._run(model_name, attempt, audio_path, duration, progress, cancel)
            except Exception as exc:
                if not is_cuda_oom(exc) and not ("cuda" in str(exc).lower() and attempt.device == "cuda"):
                    raise
                last_exc = exc
                model_manager.recover_from_oom("transcription")
                log.warning("whisper attempt %s failed (%s); trying a lighter configuration", attempt, exc)
                if progress:
                    progress(0.0, "GPU memory exhausted - retrying with lighter settings")
            finally:
                model_manager.unload(f"whisper:{model_name}:{attempt.device}:{attempt.compute_type}")
        raise GPUMemoryError("Transcription failed on all device configurations.", detail=str(last_exc))

    def _run(self, model_name: str, attempt: Attempt, audio_path: Path, duration: float | None,
             progress: ProgressFn | None, cancel: CancelToken | None) -> TranscriptResult:
        from faster_whisper import BatchedInferencePipeline, WhisperModel

        key = f"whisper:{model_name}:{attempt.device}:{attempt.compute_type}"
        spec = ALL_MODELS.get(f"whisper:{model_name}")

        def loader() -> WhisperModel:
            return WhisperModel(str(whisper_dir(self.models_root, model_name)), device=attempt.device,
                                compute_type=attempt.compute_type, cpu_threads=0, num_workers=1)

        def unloader(m: WhisperModel) -> None:
            with contextlib.suppress(Exception):
                m.model.unload_model()

        if progress:
            progress(0.0, f"Loading Whisper {model_name} on {attempt.device.upper()}")
        t0 = time.monotonic()
        model = model_manager.load(key, loader, vram_mb=spec.vram_mb if spec else 2000, unload=unloader)
        common = dict(
            language=self.settings.language or None,
            beam_size=self.settings.beam_size,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500, "speech_pad_ms": 200},
            condition_on_previous_text=False,
            temperature=[0.0, 0.2, 0.4, 0.6],
        )
        if attempt.batch_size > 0:
            pipeline = BatchedInferencePipeline(model=model)
            segments_iter, info = pipeline.transcribe(str(audio_path), batch_size=attempt.batch_size, **common)
        else:
            segments_iter, info = model.transcribe(str(audio_path), **common)
        total = duration or info.duration or 1.0
        words: list[Word] = []
        segments: list[Segment] = []
        for seg in segments_iter:
            if cancel:
                cancel.raise_if_cancelled()
            # Drop likely hallucinations over non-speech.
            if seg.no_speech_prob > 0.85 and seg.avg_logprob < -1.0:
                continue
            sidx = len(segments)
            segments.append(Segment(sidx, round(seg.start, 3), round(seg.end, 3), seg.text.strip(),
                                    round(seg.avg_logprob, 4), round(seg.no_speech_prob, 4)))
            for w in seg.words or []:
                words.append(Word(len(words), w.word, float(w.start), float(w.end), round(float(w.probability), 4),
                                  sidx))
            if progress:
                progress(min(0.99, seg.end / total), f"Transcribing ({attempt.device.upper()}, {model_name})")
        words = sanitize_words(words, info.duration, info.language)
        sentences = build_sentences(words)
        elapsed = time.monotonic() - t0
        log.info("transcribed %.0fs of audio in %.1fs (%s %s, %d words)", info.duration, elapsed, attempt.device,
                 attempt.compute_type, len(words))
        return TranscriptResult(
            language=info.language, language_probability=round(info.language_probability or 0.0, 4),
            duration=info.duration, words=words, segments=segments, sentences=sentences, model=model_name,
            device=attempt.device, compute_type=attempt.compute_type, elapsed_s=round(elapsed, 2),
        )
