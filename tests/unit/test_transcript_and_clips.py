import numpy as np

from shortforge.engines.audio.analysis import AudioFeatures, plan_silence_cuts, silences_from_speech
from shortforge.engines.clip_detection.boundaries import BoundaryConfig, refine
from shortforge.engines.clip_detection.candidates import (
    Candidate,
    GenerationConfig,
    generate_candidates,
    select_diverse,
)
from shortforge.engines.clip_detection.dedupe import ExistingClip, find_duplicate, text_similarity
from shortforge.engines.clip_detection.features import sentence_features
from shortforge.engines.clip_detection.llm_ranker import grounded_hook_text, grounded_keywords, sanitize_title
from shortforge.engines.ranking.weights import DEFAULT_WEIGHTS, combine, normalize_weights
from shortforge.engines.transcription.sentences import build_sentences, is_terminal, sanitize_words
from shortforge.engines.transcription.types import Word, words_text


def test_terminal_detection() -> None:
    assert is_terminal(" done.") and is_terminal(" really?!") and is_terminal(' "yes."')
    assert not is_terminal(" Mr.") and not is_terminal(" e.g.") and not is_terminal(" and")


def test_sanitize_merges_continuation_tokens() -> None:
    words = [Word(0, " costs", 0.0, 0.3), Word(1, " 2", 0.35, 0.5), Word(2, ",999", 0.5, 0.8), Word(3, " dollars", 0.85, 1.2)]
    out = sanitize_words(words, 10, "en")
    assert [w.text for w in out] == [" costs", " 2,999", " dollars"]
    assert out[1].end == 0.8
    assert words_text(out) == "costs 2,999 dollars"


def test_sanitize_keeps_cjk_tokens() -> None:
    words = [Word(0, "你好", 0.0, 0.3), Word(1, "世界", 0.3, 0.6)]
    assert len(sanitize_words(words, 10, "zh")) == 2


def test_sanitize_fixes_timestamps() -> None:
    out = sanitize_words([Word(0, " a", 1.0, 0.9), Word(1, " b", 0.5, 0.6)], 10, "en")
    assert all(w.end > w.start for w in out)
    assert out[1].start >= out[0].end - 0.02


def test_build_sentences(words_factory) -> None:
    words = words_factory("Hello there. How are you? I am fine and this keeps going", pauses={7: 1.5})
    sents = build_sentences(words)
    # terminal punctuation splits, and so does a long pause (before "fine")
    assert [s.text for s in sents] == ["Hello there.", "How are you?", "I am", "fine and this keeps going"]
    assert words[3].sentence_idx == 1


def test_long_run_splits_on_comma(words_factory) -> None:
    text = " ".join(["word"] * 30 + ["comma,"] + ["more"] * 30)
    sents = build_sentences(words_factory(text), max_words=45)
    assert len(sents) >= 2
    assert sents[0].text.endswith("comma,")


def _script():
    return ("Why do most people fail at this? Here's the thing nobody tells you. It's not talent, it's the "
            "system you use every single day. I tested this for 30 days and the results were insane. "
            "My output doubled and I finally understood why. That's why the system matters more than motivation. "
            "Anyway, thanks for watching and don't forget to subscribe.")


def test_candidates_are_sentence_aligned(words_factory) -> None:
    words = words_factory(_script(), word_dur=0.28, gap=0.06)
    sents = build_sentences(words)
    feats = sentence_features(sents, words)
    audio = AudioFeatures(duration=words[-1].end + 1, hop_s=0.05, energy_db=np.full(1000, -25.0, dtype=np.float32),
                          speech=[(0.0, words[-1].end)], silences=[], loudness_ref_db=-25.0)
    cands = generate_candidates(sents, feats, audio, None, [], GenerationConfig(min_duration=8, max_duration=25))
    assert cands
    starts = {s.start for s in sents}
    ends = {s.end for s in sents}
    for c in cands:
        assert c.start in starts and c.end in ends
        assert 8 <= c.duration <= 25
        assert 0 <= c.heuristic <= 100
    best = max(cands, key=lambda c: c.heuristic)
    # The question hook should outrank a span that begins with the outro.
    outro = [c for c in cands if c.text.startswith("Anyway")]
    assert all(best.heuristic > o.heuristic for o in outro)


def test_select_diverse_limits_overlap() -> None:
    def cand(s, e, score):
        return Candidate(0, 0, s, e, "", 0, 0, heuristic=score, final=score)

    chosen = select_diverse([cand(0, 30, 90), cand(2, 32, 89), cand(40, 70, 70), cand(35, 60, 60)], top_k=3)
    assert [(c.start, c.end) for c in chosen] == [(0, 30), (40, 70)]


def test_boundaries_never_cut_words(words_factory) -> None:
    words = words_factory("one two three. four five six. seven eight nine.", word_dur=0.3, gap=0.1)
    sents = build_sentences(words)
    start, end, fw, lw = refine(1, 1, sents, words, cuts=[], video_duration=20.0, cfg=BoundaryConfig())
    assert words[fw - 1].end < start <= words[fw].start
    assert words[lw].end <= end < words[lw + 1].start


def test_boundaries_snap_to_scene_cut(words_factory) -> None:
    words = words_factory("one two three. four five six.", word_dur=0.3, gap=0.4)
    sents = build_sentences(words)
    cut = words[3].start - 0.2
    start, _, _, _ = refine(1, 1, sents, words, cuts=[cut], video_duration=20.0, cfg=BoundaryConfig())
    assert start == round(cut, 3)


def test_score_combination() -> None:
    bd = combine({"hook": 90, "payoff": 80}, {"dead_air": 5})
    expected = (DEFAULT_WEIGHTS["hook"] * 90 + DEFAULT_WEIGHTS["payoff"] * 80) / (
        DEFAULT_WEIGHTS["hook"] + DEFAULT_WEIGHTS["payoff"]) - 5
    assert abs(bd.final - round(expected, 2)) < 0.01
    assert combine({"hook": 10}, {"sponsor_segment": 40}).final == 0.0
    w = normalize_weights({k: v * 3 for k, v in DEFAULT_WEIGHTS.items()})
    assert abs(sum(w.values()) - sum(DEFAULT_WEIGHTS.values())) < 0.01


def test_duplicate_detection() -> None:
    a = "this graphics card is almost twice as fast as the previous generation for gaming"
    assert text_similarity(a, a) > 0.99
    assert text_similarity(a, "cooking pasta requires salted boiling water and patience") < 0.1
    existing = [ExistingClip("short:183", 1, 100.0, 140.0, a)]
    m = find_duplicate(1, 102.0, 138.0, "totally different words here", existing, threshold=0.8)
    assert m and m.key == "short:183" and m.reason == "overlapping source range"
    m2 = find_duplicate(2, 0, 30, a + " indeed", existing, threshold=0.8)
    assert m2 and m2.reason == "transcript overlap"
    assert find_duplicate(2, 0, 30, "unrelated content entirely about cooking", existing) is None


def test_grounding_filters() -> None:
    clip = "This graphics card is almost twice as fast and it only costs 499 dollars."
    assert grounded_hook_text("Twice as fast graphics card", clip) == "Twice as fast graphics card"
    assert grounded_hook_text("Nvidia secretly admits defeat", clip) is None
    assert grounded_hook_text("one two three four five six seven eight", clip) is None
    assert grounded_keywords(["graphics card", "quantum", "499"], clip) == ["graphics card", "499"]
    assert '"' not in sanitize_title('He said "this is a scam"', clip)


def test_silence_cuts_only_between_words() -> None:
    words = [(0.0, 0.5), (0.55, 1.0), (2.5, 3.0), (3.05, 3.5)]
    ranges = plan_silence_cuts(words, 0.0, 3.6, "balanced")
    assert len(ranges) == 2
    # the cut sits inside the 1.5 s gap, keeping breathing room on both sides
    assert 1.0 < ranges[0][1] < 2.5 and 1.0 < ranges[1][0] < 2.5
    for s, e in words:
        assert any(a <= s and e <= b for a, b in ranges)
    assert plan_silence_cuts(words, 0.0, 3.6, "natural")[0][1] < plan_silence_cuts(words, 0.0, 3.6, "natural")[1][0]


def test_silences_from_speech() -> None:
    assert silences_from_speech([(1.0, 2.0), (2.1, 3.0)], 5.0) == [(0.0, 1.0), (3.0, 5.0)]
