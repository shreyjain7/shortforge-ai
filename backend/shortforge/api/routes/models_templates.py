"""Model manager and caption templates (with real rendered previews)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from shortforge.core.model_registry import ALL_MODELS
from shortforge.core.settings_store import update_settings
from shortforge.database.models import CaptionStyle
from shortforge.database.session import get_session
from shortforge.engines.captions.presets import CaptionPreset, builtin_presets, fonts_dir
from shortforge.workers.context import get_context

router = APIRouter()


# ---------------------------------------------------------------------------- models
@router.get("/models")
def list_models() -> dict[str, Any]:
    ctx = get_context()
    settings = ctx.settings()
    hw = ctx.hardware()
    items = ctx.model_store().status()
    return {"items": items, "active": {"whisper": settings.transcription.model, "llm": settings.llm.model},
            "recommended": {"whisper": hw.recommended_whisper_model, "llm": hw.recommended_llm,
                            "profile": hw.recommended_ai_profile},
            "vram_mb": hw.gpus[0].vram_total_mb if hw.gpus else 0}


@router.post("/models/{model_id:path}/install")
def install(model_id: str) -> dict[str, Any]:
    if model_id not in ALL_MODELS and not model_id.startswith("llm:"):
        raise HTTPException(404, "Unknown model")
    job = get_context().queue.enqueue("install_model", {"model_id": model_id}, priority=90,
                                      dedupe_key=f"install:{model_id}")
    return {"job_id": job}


@router.delete("/models/{model_id:path}")
def remove(model_id: str) -> dict[str, Any]:
    try:
        get_context().model_store().remove(model_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    get_context().bus.publish("models.updated", {})
    return {"ok": True}


@router.post("/models/{model_id:path}/activate")
def activate(model_id: str, s: Session = Depends(get_session)) -> dict[str, Any]:
    kind, _, name = model_id.partition(":")
    if kind == "whisper":
        update_settings(s, {"transcription": {"model": name}})
    elif kind == "llm":
        update_settings(s, {"llm": {"model": name, "provider": "ollama"}})
    else:
        raise HTTPException(422, "Only Whisper and LLM models can be activated")
    return {"ok": True}


# ---------------------------------------------------------------------------- caption templates
def _all_presets(s: Session) -> dict[str, CaptionPreset]:
    out = dict(builtin_presets())
    for row in s.execute(select(CaptionStyle)).scalars():
        try:
            out[row.name] = CaptionPreset.model_validate({**row.data, "name": row.name, "builtin": False})
        except Exception:
            continue
    return out


@router.get("/templates/captions")
def list_caption_templates(s: Session = Depends(get_session)) -> list[dict[str, Any]]:
    return [p.model_dump() for p in _all_presets(s).values()]


@router.post("/templates/captions")
def save_caption_template(body: dict[str, Any], s: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        preset = CaptionPreset.model_validate({**body, "builtin": False})
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc
    if preset.name in builtin_presets():
        raise HTTPException(409, "Built-in presets cannot be overwritten; use a new name.")
    if not (fonts_dir() / preset.font_file).exists():
        raise HTTPException(422, f"Font file {preset.font_file} is not installed.")
    row = s.execute(select(CaptionStyle).where(CaptionStyle.name == preset.name)).scalar()
    data = preset.model_dump(exclude={"builtin"})
    if row is None:
        s.add(CaptionStyle(name=preset.name, builtin=False, data=data))
    else:
        row.data = data
    return data


@router.delete("/templates/captions/{name}")
def delete_caption_template(name: str, s: Session = Depends(get_session)) -> dict[str, Any]:
    row = s.execute(select(CaptionStyle).where(CaptionStyle.name == name)).scalar()
    if row is None:
        raise HTTPException(404, "Custom preset not found")
    s.delete(row)
    return {"ok": True}


@router.get("/templates/fonts")
def fonts() -> list[str]:
    return sorted(p.name for p in fonts_dir().glob("*.ttf"))


@router.get("/templates/captions/{name}/preview.png")
def caption_preview(name: str, s: Session = Depends(get_session)) -> FileResponse:
    """Render the preset with libass over a neutral frame (exactly what the renderer produces)."""
    from shortforge.engines.captions.ass import build_ass, write_ass
    from shortforge.engines.captions.layout import CapWord, LayoutConfig
    from shortforge.engines.media import ffmpeg as ff

    presets = _all_presets(s)
    preset = presets.get(name)
    if preset is None:
        raise HTTPException(404, "Preset not found")
    key = hashlib.sha1(json.dumps(preset.model_dump(), sort_keys=True).encode()).hexdigest()[:12]
    out = get_context().paths.cache / "caption_previews" / f"{key}.png"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        sample = ["This", "graphics", "card", "is", "almost", "twice", "as", "fast!"]
        emph = [False, True, True, False, False, True, True, True]
        words = [CapWord(w, 0.1 + i * 0.25, 0.1 + i * 0.25 + 0.22, e) for i, (w, e) in enumerate(zip(sample, emph, strict=False))]
        cfg = LayoutConfig(1080, 1920)
        content, pages = build_ass(words, preset, cfg, clip_duration=4.0)
        page = next((p for p in pages if any(w.emphasis for w in p.words)), pages[0])
        active = next((w for w in page.words if w.emphasis), page.words[0])
        t = (active.start + min(active.end, page.end)) / 2 + 0.12
        work = out.parent / f"{key}_work"
        work.mkdir(exist_ok=True)
        write_ass(work / "p.ass", content)
        fd = ff.escape_filter_path(fonts_dir())
        ff.run_ffmpeg_capture(["-f", "lavfi", "-i", "gradients=s=1080x1920:c0=0x2b2d42:c1=0x1b1b2f:x0=0:y0=0:x1=1080:y1=1920:d=4",
                               "-vf", f"ass=filename='{ff.escape_filter_path(work / 'p.ass')}':fontsdir='{fd}',"
                               f"crop=1080:760:0:900,scale=540:-2",
                               "-ss", f"{t:.2f}", "-frames:v", "1", str(out)])
    return FileResponse(out, media_type="image/png", headers={"Cache-Control": "max-age=3600"})
