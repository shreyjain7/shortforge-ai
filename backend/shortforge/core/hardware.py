"""Hardware detection, live resource monitoring and profile recommendations."""

from __future__ import annotations

import contextlib
import os
import platform
import shutil
import threading
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

import psutil

from shortforge.core.logging import get_logger

log = get_logger("hardware")


@dataclass
class GPUInfo:
    index: int
    name: str
    vram_total_mb: int
    driver: str | None = None
    cuda_capability: str | None = None


@dataclass
class HardwareReport:
    os: str
    cpu_name: str
    cpu_cores: int
    cpu_threads: int
    ram_total_gb: float
    gpus: list[GPUInfo] = field(default_factory=list)
    cuda_available: bool = False
    cuda_device_count: int = 0
    ffmpeg_path: str | None = None
    ffmpeg_version: str | None = None
    nvenc: dict[str, bool] = field(default_factory=dict)
    hw_encoders: list[str] = field(default_factory=list)
    storage_free_gb: float = 0.0
    storage_total_gb: float = 0.0
    recommended_ai_profile: str = "BALANCED"
    recommended_render_profile: str = "BALANCED"
    recommended_whisper_model: str = "small"
    recommended_compute_type: str = "int8"
    recommended_llm: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _cpu_name() -> str:
    if os.name == "nt":
        try:
            import winreg

            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            value, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            return str(value).strip()
        except OSError:
            pass
    elif Path("/proc/cpuinfo").exists():
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    return platform.processor() or "Unknown CPU"


_nvml_lock = threading.Lock()
_nvml_ready: bool | None = None


def _nvml():  # type: ignore[no-untyped-def]
    global _nvml_ready
    with _nvml_lock:
        if _nvml_ready is False:
            return None
        try:
            import pynvml

            if not _nvml_ready:
                pynvml.nvmlInit()
                _nvml_ready = True
            return pynvml
        except Exception as exc:  # pragma: no cover - depends on driver
            log.info("NVML unavailable: %s", exc)
            _nvml_ready = False
            return None


def detect_gpus() -> list[GPUInfo]:
    nv = _nvml()
    if nv is None:
        return []
    gpus: list[GPUInfo] = []
    try:
        driver = nv.nvmlSystemGetDriverVersion()
        driver = driver.decode() if isinstance(driver, bytes) else driver
        for i in range(nv.nvmlDeviceGetCount()):
            h = nv.nvmlDeviceGetHandleByIndex(i)
            name = nv.nvmlDeviceGetName(h)
            name = name.decode() if isinstance(name, bytes) else name
            mem = nv.nvmlDeviceGetMemoryInfo(h)
            try:
                major, minor = nv.nvmlDeviceGetCudaComputeCapability(h)
                cap = f"{major}.{minor}"
            except Exception:
                cap = None
            gpus.append(GPUInfo(i, name, int(mem.total / 1024 / 1024), driver, cap))
    except Exception as exc:  # pragma: no cover
        log.warning("GPU enumeration failed: %s", exc)
    return gpus


def cuda_device_count() -> int:
    try:
        from shortforge.engines.transcription.cuda_dlls import ensure_cuda_dlls

        ensure_cuda_dlls()
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count())
    except Exception:
        return 0


@dataclass
class LiveStats:
    cpu_percent: float
    ram_used_gb: float
    ram_total_gb: float
    ram_percent: float
    gpu_util: float | None = None
    vram_used_mb: float | None = None
    vram_total_mb: float | None = None
    gpu_temp_c: float | None = None
    gpu_power_w: float | None = None
    encoder_util: float | None = None


def gpu_memory(index: int = 0) -> tuple[int, int] | None:
    """(used_mb, total_mb) for a GPU, or None."""
    nv = _nvml()
    if nv is None:
        return None
    try:
        h = nv.nvmlDeviceGetHandleByIndex(index)
        mem = nv.nvmlDeviceGetMemoryInfo(h)
        return int(mem.used / 1024 / 1024), int(mem.total / 1024 / 1024)
    except Exception:
        return None


def live_stats() -> LiveStats:
    vm = psutil.virtual_memory()
    stats = LiveStats(
        cpu_percent=psutil.cpu_percent(interval=None),
        ram_used_gb=round((vm.total - vm.available) / 1024**3, 2),
        ram_total_gb=round(vm.total / 1024**3, 2),
        ram_percent=vm.percent,
    )
    nv = _nvml()
    if nv is not None:
        try:
            h = nv.nvmlDeviceGetHandleByIndex(0)
            util = nv.nvmlDeviceGetUtilizationRates(h)
            mem = nv.nvmlDeviceGetMemoryInfo(h)
            stats.gpu_util = float(util.gpu)
            stats.vram_used_mb = round(mem.used / 1024 / 1024)
            stats.vram_total_mb = round(mem.total / 1024 / 1024)
            with contextlib.suppress(Exception):
                stats.gpu_temp_c = float(nv.nvmlDeviceGetTemperature(h, nv.NVML_TEMPERATURE_GPU))
            with contextlib.suppress(Exception):
                stats.gpu_power_w = round(nv.nvmlDeviceGetPowerUsage(h) / 1000.0, 1)
            try:
                enc, _ = nv.nvmlDeviceGetEncoderUtilization(h)
                stats.encoder_util = float(enc)
            except Exception:
                pass
        except Exception:
            pass
    return stats


def recommend(vram_mb: int, ram_gb: float, cuda: bool) -> dict[str, str | None]:
    """Map available hardware to AI/render profiles and default models.

    VRAM budgets (approx., faster-whisper/CTranslate2):
      large-v3 fp16 ~4.5 GB, large-v3 int8_float16 ~3 GB, large-v3-turbo fp16 ~2.5 GB,
      small ~1 GB. A 7-8B Q4 LLM needs ~5-6 GB, so models are loaded one at a time.
    """
    if cuda and vram_mb >= 10_000:
        return {"ai": "QUALITY", "render": "ULTRA", "whisper": "large-v3", "compute": "float16",
                "llm": "qwen2.5:14b" if vram_mb >= 14_000 else "qwen2.5:7b"}
    if cuda and vram_mb >= 7_000:
        return {"ai": "BALANCED", "render": "ULTRA", "whisper": "large-v3-turbo", "compute": "float16",
                "llm": "qwen2.5:7b"}
    if cuda and vram_mb >= 4_000:
        return {"ai": "BALANCED", "render": "BALANCED", "whisper": "small", "compute": "int8_float16",
                "llm": "qwen2.5:3b" if ram_gb < 16 else "qwen2.5:7b"}
    return {"ai": "LOW", "render": "FAST", "whisper": "base" if ram_gb < 12 else "small", "compute": "int8",
            "llm": "llama3.2:3b" if ram_gb >= 8 else None}


@lru_cache(maxsize=1)
def _static_report(data_root: str) -> HardwareReport:
    from shortforge.engines.media import ffmpeg as ff

    vm = psutil.virtual_memory()
    gpus = detect_gpus()
    cuda_count = cuda_device_count()
    ff_paths = ff.find_ffmpeg()
    report = HardwareReport(
        os=f"{platform.system()} {platform.release()} ({platform.version()})",
        cpu_name=_cpu_name(),
        cpu_cores=psutil.cpu_count(logical=False) or 1,
        cpu_threads=psutil.cpu_count(logical=True) or 1,
        ram_total_gb=round(vm.total / 1024**3, 1),
        gpus=gpus,
        cuda_available=cuda_count > 0,
        cuda_device_count=cuda_count,
        ffmpeg_path=ff_paths[0] if ff_paths else None,
        ffmpeg_version=ff.ffmpeg_version(),
    )
    if ff_paths:
        report.nvenc = {c: ff.encoder_works(c) for c in ("h264_nvenc", "hevc_nvenc", "av1_nvenc")}
        report.hw_encoders = sorted(
            e for e in ff.available_encoders() if e.endswith(("_nvenc", "_qsv", "_amf", "_mf"))
        )
    else:
        report.notes.append("FFmpeg not found - install it to enable rendering.")
    try:
        usage = shutil.disk_usage(data_root)
        report.storage_free_gb = round(usage.free / 1024**3, 1)
        report.storage_total_gb = round(usage.total / 1024**3, 1)
    except OSError:
        pass
    vram = gpus[0].vram_total_mb if gpus else 0
    rec = recommend(vram, report.ram_total_gb, report.cuda_available)
    report.recommended_ai_profile = str(rec["ai"])
    report.recommended_render_profile = str(rec["render"]) if any(report.nvenc.values()) else "BALANCED"
    report.recommended_whisper_model = str(rec["whisper"])
    report.recommended_compute_type = str(rec["compute"])
    report.recommended_llm = rec["llm"]
    if gpus and not report.cuda_available:
        report.notes.append("An NVIDIA GPU was found but CUDA libraries could not be loaded; AI runs on CPU.")
    if report.ram_total_gb < 16:
        report.notes.append("Less than 16 GB RAM: large LLMs are unloaded between stages to stay within memory.")
    return report


def hardware_report(data_root: Path, *, refresh: bool = False) -> HardwareReport:
    if refresh:
        _static_report.cache_clear()
    return _static_report(str(data_root))
