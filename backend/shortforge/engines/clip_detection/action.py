"""Candidate generation for low-speech videos (action / POV / sports / music-driven footage).

When there is too little speech for sentence-based clipping, clips are built from scene-cut-aligned
windows and scored on what makes wordless footage watchable: motion intensity, audio energy peaks
(impacts, crowd, music drops), editing rhythm, and a strong beginning and ending. Boundaries sit on
scene cuts where possible and never cut through the few spoken words that exist.
"""

from __future__ import annotations

import itertools

import numpy as np

from shortforge.engines.audio.analysis import AudioFeatures
from shortforge.engines.clip_detection.candidates import Candidate, GenerationConfig, TimelineContext
from shortforge.engines.clip_detection.features import clamp, sigmoid01
from shortforge.engines.ranking.weights import combine
from shortforge.engines.transcription.types import Word, words_text

# Metrics that carry information for wordless footage (others are omitted, not faked).
ACTION_WEIGHTS = {
    "hook": 1.6, "visual_activity": 1.6, "emotion": 1.0, "payoff": 1.2, "pacing": 1.0, "story_completeness": 0.6,
    "standalone": 0.8, "shareability": 0.8, "rewatch": 0.8,
}


def speech_ratio(audio: AudioFeatures | None, duration: float) -> float:
    return audio.speech_ratio(0.0, duration) if audio and duration else 0.0


def _activity(activity: np.ndarray | None, a: float, b: float) -> np.ndarray:
    if activity is None or not len(activity):
        return np.zeros(1)
    lo, hi = max(0, int(a)), min(len(activity), max(int(a) + 1, int(np.ceil(b))))
    seg = activity[lo:hi]
    return seg if len(seg) else np.zeros(1)


def _snap_off_words(t: float, words: list[Word], toward: str) -> float:
    """Move a boundary out of any word it would cut through."""
    for w in words:
        if w.start < t < w.end:
            return w.start - 0.05 if toward == "start" else w.end + 0.1
    return t


def generate_action_candidates(duration: float, cuts: list[float], activity: np.ndarray | None,
                               audio: AudioFeatures | None, words: list[Word], cfg: GenerationConfig,
                               timeline: TimelineContext | None = None) -> list[Candidate]:
    bounds = sorted({0.0, *[c for c in cuts if 0 < c < duration], duration})
    # Very long shots: add synthetic boundaries every ~4 s so windows can still be formed.
    dense: list[float] = []
    for a, b in itertools.pairwise(bounds):
        dense.append(a)
        n = int((b - a) // 4.0)
        dense += [a + (b - a) * k / (n + 1) for k in range(1, n + 1)]
    dense.append(duration)
    ref = audio.loudness_ref_db if audio else -25.0
    all_act = activity if activity is not None and len(activity) else np.zeros(1)
    act_ref = float(np.percentile(all_act, 75)) + 1e-6
    out: list[Candidate] = []
    target = min(cfg.max_duration, max(cfg.min_duration, 25.0))
    for i, s in enumerate(dense):
        for e in dense[i + 1 :]:
            dur = e - s
            if dur < cfg.min_duration:
                continue
            if dur > cfg.max_duration:
                break
            start = max(0.0, _snap_off_words(s, words, "start"))
            end = min(duration, _snap_off_words(e, words, "end"))
            seg = _activity(all_act, start, end)
            head = _activity(all_act, start, start + 3)
            tail = _activity(all_act, end - 3, end)
            motion = float(seg.mean())
            peak = float(np.percentile(seg, 90))
            energy = audio.energy_at(start, end) if audio else -30.0
            e_var = audio.energy_variation(start, end) if audio else 3.0
            head_energy = audio.energy_at(start, start + 3) if audio else energy
            tail_energy = audio.energy_at(end - 3, end) if audio else energy
            n_cuts = sum(1 for c in cuts if start < c < end)
            cut_rate = n_cuts / dur * 10
            speech_share = audio.speech_ratio(start, end) if audio else 0.0

            hook = clamp(25 + 55 * min(1.0, float(head.mean()) / act_ref) + 20 * (sigmoid01(head_energy - ref, 0, 3) - 0.3))
            visual = clamp(20 + 50 * min(1.2, motion / act_ref) + 20 * min(1.0, peak / act_ref))
            emotion = clamp(30 + 6 * e_var + 15 * (sigmoid01(energy - ref, 0, 3) - 0.5))
            payoff = clamp(30 + 45 * min(1.2, float(tail.max()) / act_ref) + 15 * (sigmoid01(tail_energy - ref, 0, 3) - 0.3))
            pacing = clamp(100 - 18 * abs(cut_rate - 3.0))  # ~3 cuts / 10 s feels energetic but readable
            story = clamp(55 + 20 * (1 - abs(dur - target) / max(10.0, target)))
            share = clamp(0.5 * visual + 0.5 * hook)
            metrics = {"hook": hook, "visual_activity": visual, "emotion": emotion, "payoff": payoff,
                       "pacing": pacing, "story_completeness": story, "standalone": 80.0,
                       "shareability": share, "rewatch": clamp(0.6 * payoff + 0.4 * visual)}
            penalties: dict[str, float] = {}
            if motion < 0.25 * act_ref:
                penalties["low_visual_interest"] = 12.0
            if speech_share > 0.3:
                penalties["missing_context"] = 6.0  # partial talking inside an action clip rarely stands alone
            sponsor = timeline.label_overlap(start, end, "SPONSOR") if timeline else 0.0
            outro = timeline.label_overlap(start, end, "OUTRO") if timeline else 0.0
            if sponsor > 0.15 or outro > 0.3:
                penalties["sponsor_segment"] = round(30 * max(sponsor, outro) + 10, 2)
            bd = combine(metrics, penalties, ACTION_WEIGHTS)
            spoken = [w for w in words if start <= w.start < end]
            out.append(Candidate(
                first_sentence=-1, last_sentence=-1, start=round(start, 3), end=round(end, 3),
                text=words_text(spoken), first_word=spoken[0].idx if spoken else -1,
                last_word=spoken[-1].idx if spoken else -1, metrics=metrics, penalties=penalties,
                heuristic=bd.final, final=bd.final, breakdown=bd, notes=["action mode (low speech)"],
            ))
    return out
