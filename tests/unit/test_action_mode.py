import numpy as np

from shortforge.engines.audio.analysis import AudioFeatures
from shortforge.engines.clip_detection.action import generate_action_candidates
from shortforge.engines.clip_detection.candidates import GenerationConfig, TimelineContext
from shortforge.engines.transcription.types import Word


def test_action_mode_prefers_high_motion_and_skips_outro_plug() -> None:
    duration = 120.0
    activity = np.full(121, 0.02, dtype=np.float32)
    activity[40:65] = 0.3  # the big jump sequence
    energy = np.full(int(duration / 0.05), -30.0, dtype=np.float32)
    energy[int(45 / 0.05):int(60 / 0.05)] = -18.0
    audio = AudioFeatures(duration, 0.05, energy, speech=[(100.0, 115.0)], silences=[], loudness_ref_db=-28.0)
    cuts = [float(x) for x in range(5, 120, 5)]
    words = [Word(0, " follow", 100.2, 100.5), Word(1, " us", 100.5, 100.7)]
    timeline = TimelineContext([(100.0, 115.0, "SPONSOR", 10.0)])
    cands = generate_action_candidates(duration, cuts, activity, audio, words, GenerationConfig(15, 45, 25), timeline)
    assert cands
    best = max(cands, key=lambda c: c.final)
    assert 30 <= best.start <= 65 and best.end <= 80
    plug = [c for c in cands if c.start >= 95]
    assert all(c.final < best.final - 10 for c in plug)
    for c in cands:  # never cuts through a spoken word
        for w in words:
            assert not (w.start < c.start < w.end) and not (w.start < c.end < w.end)
