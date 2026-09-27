"""PASS 4 - local-LLM semantic evaluation of the strongest heuristic candidates.

The LLM judges what text statistics cannot: whether the clip makes sense to a stranger, whether
the opening creates intrigue, whether the payoff lands. Every generated string is validated
against the transcript so no fabricated quotes or facts can leak into the output.
"""

from __future__ import annotations

import re
from typing import Any

from shortforge.core.logging import get_logger
from shortforge.engines.clip_detection.candidates import Candidate
from shortforge.engines.clip_detection.features import content_tokens, tokens
from shortforge.engines.llm.base import LLMProvider
from shortforge.engines.transcription.types import Sentence

log = get_logger("llm_ranker")

LLM_METRICS = ("hook", "curiosity", "standalone", "emotion", "payoff", "story_completeness", "shareability",
               "rewatch", "information_density")
HOOK_TYPES = ["question", "bold_claim", "surprising_fact", "story", "reaction", "argument", "humor", "how_to",
              "statistic", "none"]
CATEGORIES = ["education", "tech", "science", "humor", "story", "motivation", "news", "gaming", "podcast", "business",
              "lifestyle", "sports", "music", "other"]

SYSTEM = (
    "You are a senior short-form video editor evaluating whether a transcript excerpt from a long video would work "
    "as a standalone YouTube Short / TikTok for viewers who never saw the original. Be critical and calibrated: "
    "typical excerpts score 40-70; reserve 85+ for genuinely exceptional ones. Scores are integers 0-100.\n"
    "hook: do the first 3 seconds grab attention? curiosity: does it open a question the viewer wants answered? "
    "standalone: understandable without prior context (penalise dangling references like 'that', 'he', 'as I said'). "
    "emotion: emotional intensity. payoff: does it end on a satisfying answer, punchline, reveal or conclusion? "
    "story_completeness: clear beginning, development and ending. shareability: would people send it to a friend? "
    "rewatch: would people watch it twice? information_density: useful or interesting information per second.\n"
    "trim_start: number of weak leading clip sentences to drop (0-2). trim_end: trailing sentences to drop (0-2). "
    "extend_end: number of the AFTER sentences to include because the payoff is there (0-3).\n"
    "title: under 60 characters, faithful to what is actually said, no clickbait lies. hook_text: optional on-screen "
    "opening caption of at most 7 words built ONLY from ideas explicitly stated in the clip (empty string if none). "
    "keywords: up to 6 important words or short phrases copied verbatim from the clip. Never invent facts or quotes."
)

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        **{m: {"type": "integer"} for m in LLM_METRICS},
        "hook_type": {"type": "string", "enum": HOOK_TYPES},
        "category": {"type": "string", "enum": CATEGORIES},
        "topic": {"type": "string"},
        "trim_start": {"type": "integer"},
        "trim_end": {"type": "integer"},
        "extend_end": {"type": "integer"},
        "title": {"type": "string"},
        "hook_text": {"type": "string"},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
    },
    "required": [*LLM_METRICS, "hook_type", "category", "topic", "trim_start", "trim_end", "extend_end", "title",
                 "hook_text", "keywords", "reason"],
}


def build_prompt(c: Candidate, sentences: list[Sentence], video_title: str, channel: str | None) -> str:
    before = sentences[max(0, c.first_sentence - 2) : c.first_sentence]
    clip = sentences[c.first_sentence : c.last_sentence + 1]
    after = sentences[c.last_sentence + 1 : c.last_sentence + 4]
    parts = [f'Source video: "{video_title}"' + (f" by {channel}" if channel else "")]
    if before:
        parts.append("CONTEXT BEFORE (not in the clip):\n" + "\n".join(f"  {s.text}" for s in before))
    parts.append(f"CLIP ({c.duration:.0f} seconds):\n" + "\n".join(f"  [{k + 1}] {s.text}" for k, s in enumerate(clip)))
    if after:
        parts.append("AFTER (not in the clip):\n" + "\n".join(f"  [A{k + 1}] {s.text}" for k, s in enumerate(after)))
    parts.append("Evaluate the CLIP. Respond with JSON only.")
    return "\n\n".join(parts)


def grounded_hook_text(text: str, clip_text: str, max_words: int = 7) -> str | None:
    """Accept an on-screen hook only if its content words come from the clip itself."""
    text = re.sub(r"\s+", " ", (text or "").strip().strip('"').strip())
    if not text:
        return None
    words = text.split()
    if len(words) > max_words:
        return None
    clip_tokens = set(tokens(clip_text))
    content = content_tokens(text)
    if not content:
        return None
    supported = sum(1 for t in content if t in clip_tokens or t.rstrip("s") in clip_tokens)
    if supported / len(content) < 0.75:
        return None
    return text


def grounded_keywords(keywords: list[str], clip_text: str, limit: int = 6) -> list[str]:
    low = clip_text.lower()
    out = []
    for k in keywords or []:
        k = str(k).strip().strip('"')
        if 2 <= len(k) <= 40 and k.lower() in low and k.lower() not in (x.lower() for x in out):
            out.append(k)
        if len(out) >= limit:
            break
    return out


def sanitize_title(title: str, clip_text: str) -> str:
    title = re.sub(r"\s+", " ", (title or "").strip()).strip()
    if len(title) > 1 and title[0] in "\"“" and title[-1] in "\"”" and title.count('"') + title.count("“") <= 2:
        title = title[1:-1].strip()  # the whole title was wrapped in quotes
    # Drop quotation marks around anything not actually said in the clip (no fabricated quotes).
    for quoted in re.findall(r"[\"“](.+?)[\"”]", title):
        if quoted.lower() not in clip_text.lower():
            title = title.replace(f'"{quoted}"', quoted).replace(f"“{quoted}”", quoted)
    if (title.count('"') + title.count("“") + title.count("”")) % 2:
        title = title.replace('"', "").replace("“", "").replace("”", "")
    return title[:95]


def evaluate(llm: LLMProvider, c: Candidate, sentences: list[Sentence], video_title: str,
             channel: str | None) -> dict[str, Any]:
    data = llm.chat_json(SYSTEM, build_prompt(c, sentences, video_title, channel), schema=SCHEMA, max_tokens=600)
    clip_text = c.text
    result: dict[str, Any] = {}
    for m in LLM_METRICS:
        try:
            result[m] = float(max(0, min(100, int(data.get(m, 50)))))
        except (TypeError, ValueError):
            result[m] = 50.0
    n_clip = c.last_sentence - c.first_sentence + 1
    result["trim_start"] = max(0, min(2, int(data.get("trim_start", 0) or 0), n_clip - 1))
    result["trim_end"] = max(0, min(2, int(data.get("trim_end", 0) or 0), n_clip - 1 - result["trim_start"]))
    result["extend_end"] = max(0, min(3, int(data.get("extend_end", 0) or 0)))
    result["hook_type"] = data.get("hook_type") if data.get("hook_type") in HOOK_TYPES else "none"
    result["category"] = data.get("category") if data.get("category") in CATEGORIES else "other"
    result["topic"] = str(data.get("topic", ""))[:80]
    result["title"] = sanitize_title(str(data.get("title", "")), clip_text)
    result["hook_text"] = grounded_hook_text(str(data.get("hook_text", "")), clip_text)
    result["keywords"] = grounded_keywords(list(data.get("keywords") or []), clip_text)
    result["reason"] = str(data.get("reason", ""))[:400]
    result["model"] = llm.model
    return result
