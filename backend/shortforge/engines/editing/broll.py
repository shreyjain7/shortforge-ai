"""Local B-roll library: automatic tagging and meaning-based matching against the transcript.

Tagging uses the folder/file names (mapped through a synonym vocabulary) and, when a local
vision-language model is installed in Ollama (e.g. qwen2.5-vl / qwen3-vl / gemma3 / llava),
a caption of the clip's middle frame. Matching places short cutaways at moments where the speaker
says something the clip depicts, never over the opening hook and never back-to-back.
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from shortforge.core.logging import get_logger
from shortforge.engines.clip_detection.lexicon import STOPWORDS
from shortforge.engines.editing.timeline import BrollInsert, CaptionWord
from shortforge.engines.media import ffmpeg as ff

log = get_logger("broll")

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}

# Semantic labels and the words that evoke them.
VOCAB: dict[str, set[str]] = {
    "car": {"car", "cars", "drive", "driving", "vehicle", "tesla", "road", "traffic", "engine", "ev", "truck"},
    "computer": {"computer", "laptop", "pc", "code", "coding", "software", "keyboard", "desktop", "programming", "developer"},
    "city": {"city", "cities", "street", "downtown", "building", "buildings", "urban", "skyline", "town"},
    "money": {"money", "cash", "dollar", "dollars", "price", "prices", "cost", "costs", "expensive", "cheap", "pay", "paid",
              "salary", "rich", "invest", "investing", "investment", "stock", "stocks", "bank", "financing", "budget"},
    "phone": {"phone", "phones", "iphone", "android", "smartphone", "mobile", "app", "apps", "screen", "foldable"},
    "gaming": {"game", "games", "gaming", "gamer", "console", "playstation", "xbox", "nintendo", "controller"},
    "space": {"space", "planet", "planets", "rocket", "nasa", "moon", "mars", "galaxy", "universe", "star", "stars", "orbit", "satellite"},
    "food": {"food", "eat", "eating", "cook", "cooking", "restaurant", "meal", "kitchen", "coffee", "pizza"},
    "travel": {"travel", "trip", "flight", "plane", "airport", "vacation", "beach", "hotel", "country", "countries"},
    "technology": {"technology", "tech", "ai", "chip", "chips", "robot", "robots", "gadget", "device", "devices", "electronics", "battery"},
    "nature": {"nature", "forest", "tree", "trees", "ocean", "mountain", "mountains", "river", "animal", "animals", "weather"},
    "people": {"people", "crowd", "friends", "family", "team", "audience", "community"},
    "office": {"office", "work", "job", "meeting", "business", "company", "boss", "startup", "career"},
    "sports": {"sport", "sports", "football", "soccer", "basketball", "gym", "workout", "fitness", "run", "running"},
    "music": {"music", "song", "songs", "band", "guitar", "piano", "concert", "album"},
}
WORD_TO_TAG = {w: tag for tag, words in VOCAB.items() for w in words}


@dataclass
class BrollClip:
    path: str
    duration: float
    tags: list[str] = field(default_factory=list)
    caption: str | None = None
    mtime: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def tags_from_text(text: str) -> set[str]:
    toks = re.findall(r"[a-z]+", text.lower())
    tags = {WORD_TO_TAG[t] for t in toks if t in WORD_TO_TAG}
    tags |= {t for t in toks if t in VOCAB}
    return tags


def _middle_frame_jpeg(path: Path, duration: float) -> bytes | None:
    import subprocess

    cmd = [ff.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-ss", f"{duration / 2:.2f}", "-i", str(path),
           "-frames:v", "1", "-vf", "scale=448:-2", "-f", "image2", "-c:v", "mjpeg", "pipe:1"]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=60, creationflags=ff.CREATE_NO_WINDOW).stdout
        return out or None
    except Exception:
        return None


VisionCaptioner = Callable[[bytes], str | None]


def ollama_vision_captioner(base_url: str, installed: list[str]) -> VisionCaptioner | None:
    """Return a captioner using a locally installed vision-language model, if any."""
    import httpx

    model = next((m for m in installed if any(k in m for k in ("-vl", "llava", "vision", "moondream", "gemma3"))), None)
    if not model:
        return None

    def caption(jpeg: bytes) -> str | None:
        try:
            r = httpx.post(f"{base_url.rstrip('/')}/api/generate", timeout=120, json={
                "model": model, "stream": False, "keep_alive": 0,
                "prompt": "Describe this video frame in one short sentence listing the main objects and setting.",
                "images": [base64.b64encode(jpeg).decode()], "options": {"temperature": 0.1, "num_predict": 60},
                **({"think": False} if model.startswith("qwen3") else {})})
            return r.json().get("response", "").strip() or None
        except Exception as exc:
            log.debug("vision caption failed: %s", exc)
            return None

    return caption


def index_library(folder: Path, cache_file: Path, captioner: VisionCaptioner | None = None) -> list[BrollClip]:
    cache: dict[str, dict] = {}
    if cache_file.exists():
        try:
            cache = json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cache = {}
    clips: list[BrollClip] = []
    changed = False
    for p in sorted(folder.rglob("*")):
        if p.suffix.lower() not in VIDEO_EXT or not p.is_file():
            continue
        entry = cache.get(str(p))
        if entry and abs(entry.get("mtime", 0) - p.stat().st_mtime) < 1 and (entry.get("caption") or captioner is None):
            clips.append(BrollClip(**entry))
            continue
        try:
            dur = ff.probe(p).duration
        except ff.FFmpegError:
            continue
        rel = str(p.relative_to(folder))
        tags = tags_from_text(rel.replace("_", " ").replace("-", " "))
        caption = None
        if captioner is not None:
            jpeg = _middle_frame_jpeg(p, dur)
            caption = captioner(jpeg) if jpeg else None
            if caption:
                tags |= tags_from_text(caption)
        clip = BrollClip(str(p), dur, sorted(tags), caption, p.stat().st_mtime)
        cache[str(p)] = clip.to_dict()
        clips.append(clip)
        changed = True
    if changed:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(cache), encoding="utf-8")
    return clips


def match_broll(words: list[CaptionWord], clips: list[BrollClip], *, total: float, max_inserts: int = 2,
                insert_duration: float = 2.0, avoid_start: float = 2.8, min_gap: float = 5.0,
                mode: str = "full") -> list[BrollInsert]:
    """Place cutaways where a spoken word evokes a tag of an available clip."""
    tagged = [c for c in clips if c.tags and c.duration >= 1.0]
    if not tagged or max_inserts <= 0:
        return []
    candidates: list[tuple[float, float, BrollClip, str]] = []
    for w in words:
        tok = re.sub(r"[^a-z]", "", w.text.lower())
        if not tok or tok in STOPWORDS:
            continue
        tag = WORD_TO_TAG.get(tok) or (tok if tok in VOCAB else None)
        if not tag or w.start < avoid_start or w.start > total - insert_duration - 1.0:
            continue
        pool = [c for c in tagged if tag in c.tags]
        if not pool:
            continue
        weight = 2.0 if w.emphasis else 1.0
        candidates.append((weight, w.start, pool[0], tag))
    chosen: list[BrollInsert] = []
    used: set[str] = set()
    for _, start, clip, tag in sorted(candidates, key=lambda x: (-x[0], x[1])):
        if len(chosen) >= max_inserts:
            break
        if clip.path in used or any(abs(start - b.start) < min_gap for b in chosen):
            continue
        dur = min(insert_duration, clip.duration)
        begin = max(0.0, start - 0.15)
        chosen.append(BrollInsert(path=clip.path, start=round(begin, 3), end=round(min(total, begin + dur), 3),
                                  source_start=round(max(0.0, (clip.duration - dur) / 2), 3), mode=mode,
                                  label=f"{tag}: {Path(clip.path).name}"))
        used.add(clip.path)
    return sorted(chosen, key=lambda b: b.start)
