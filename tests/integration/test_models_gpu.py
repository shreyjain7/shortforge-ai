"""Model lifecycle manager, OOM recovery ladder and hardware recommendations (no GPU required)."""

from __future__ import annotations

import pytest

from shortforge.core.config import TranscriptionSettings
from shortforge.core.errors import DependencyMissing, is_cuda_oom
from shortforge.core.gpu import ModelLifecycleManager
from shortforge.core.hardware import recommend
from shortforge.engines.transcription.whisper import attempt_ladder, choose_model


def test_lifecycle_exclusive_loading_unloads_previous() -> None:
    mm = ModelLifecycleManager()
    unloaded: list[str] = []
    mm.load("whisper", lambda: "W", vram_mb=2500, unload=lambda o: unloaded.append(o))
    assert mm.get("whisper") == "W"
    mm.load("llm", lambda: "L", vram_mb=5500, unload=lambda o: unloaded.append(o))
    assert unloaded == ["W"] and mm.get("whisper") is None and mm.get("llm") == "L"
    assert mm.load("llm", lambda: "other", vram_mb=1) == "L"  # cached, not reloaded
    events: list[str] = []
    mm.on_event = lambda cat, body: events.append(cat)
    mm.recover_from_oom("test")
    assert mm.loaded() == [] and events == ["gpu_recovery"]


def test_oom_detection() -> None:
    assert is_cuda_oom(RuntimeError("CUDA failed with error out of memory"))
    assert is_cuda_oom(RuntimeError("CUBLAS_STATUS_ALLOC_FAILED"))
    assert not is_cuda_oom(RuntimeError("file not found"))


def test_attempt_ladder_degrades_to_cpu() -> None:
    ladder = attempt_ladder("auto", "auto", 8, cuda_ok=True)
    assert [(a.device, a.compute_type) for a in ladder] == [("cuda", "float16"), ("cuda", "int8_float16"),
                                                            ("cuda", "int8_float16"), ("cpu", "int8")]
    assert ladder[1].batch_size == 4 and ladder[2].batch_size == 0
    assert [a.device for a in attempt_ladder("auto", "auto", 8, cuda_ok=False)] == ["cpu"]


def test_model_choice_requires_installation(tmp_path) -> None:
    with pytest.raises(DependencyMissing) as exc:
        choose_model(tmp_path, TranscriptionSettings(), "large-v3-turbo")
    assert "MB" in exc.value.message  # size is shown before any download
    d = tmp_path / "whisper" / "small"
    d.mkdir(parents=True)
    (d / "model.bin").write_bytes(b"x")
    (d / "config.json").write_text("{}")
    assert choose_model(tmp_path, TranscriptionSettings(), "large-v3-turbo") == "small"


@pytest.mark.parametrize(("vram", "ram", "cuda", "whisper", "ai"), [
    (8188, 16, True, "large-v3-turbo", "BALANCED"),
    (12288, 32, True, "large-v3", "QUALITY"),
    (6144, 16, True, "small", "BALANCED"),
    (0, 16, False, "small", "LOW"),
])
def test_recommendations(vram, ram, cuda, whisper, ai) -> None:
    rec = recommend(vram, ram, cuda)
    assert rec["whisper"] == whisper and rec["ai"] == ai


def test_encoder_selection_falls_back_to_cpu(monkeypatch) -> None:
    from shortforge.engines.media import ffmpeg as ff

    monkeypatch.setattr(ff, "encoder_works", lambda name: False)
    monkeypatch.setattr(ff, "available_encoders", lambda: frozenset({"libx264"}))
    choice = ff.choose_video_encoder("h264", "auto", "ULTRA")
    assert choice.name == "libx264" and not choice.hardware and "-crf" in choice.args
