from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from shortforge.engines.editing.broll import BrollClip, match_broll, tags_from_text
from shortforge.engines.editing.timeline import CaptionWord
from shortforge.engines.media import ffmpeg as ff
from tests.conftest import requires_ffmpeg


@requires_ffmpeg
def test_music_tempo_and_beats(tmp_path: Path) -> None:
    from shortforge.engines.audio.music import analyze_track, pick_track, snap_to_beats

    # 120 BPM click track: a short 1 kHz burst every 0.5 s.
    out = tmp_path / "click.wav"
    subprocess.run([ff.ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "aevalsrc='if(lt(mod(t\\,0.5)\\,0.03)\\,sin(2*PI*1000*t)\\,0)':s=22050:d=20", str(out)], check=True)
    info = analyze_track(out)
    assert abs(info.duration - 20) < 0.2
    assert abs(info.tempo - 120) < 6 or abs(info.tempo - 60) < 3 or abs(info.tempo - 240) < 12
    diffs = [b - a for a, b in zip(info.beats, info.beats[1:], strict=False)]
    assert diffs and abs(sorted(diffs)[len(diffs) // 2] - 60 / info.tempo) < 0.05
    assert pick_track([info], clip_duration=15) is info
    snapped = snap_to_beats([1.1, 3.0], info.beats, offset=0.0)
    assert any(abs(s - b) < 1e-6 for s in snapped for b in info.beats)


def test_broll_tagging_and_matching() -> None:
    assert tags_from_text("stock footage/city_night_drive.mp4") >= {"city", "car"}
    assert "money" in tags_from_text("A man counting cash and dollars at a bank")
    clips = [BrollClip("C:/broll/city.mp4", 8.0, ["city"]), BrollClip("C:/broll/money.mp4", 6.0, ["money"])]
    words = [CaptionWord(text=w, start=i * 0.4, end=i * 0.4 + 0.3, emphasis=(w == "price"))
             for i, w in enumerate(["so", "the", "price", "in", "every", "city", "went", "up", "and", "money", "got", "tight", "for", "everyone", "here"])]
    inserts = match_broll(words, clips, total=12.0, max_inserts=2, insert_duration=2.0, avoid_start=0.5, min_gap=2.0)
    assert 1 <= len(inserts) <= 2
    assert inserts[0].path.endswith("money.mp4")  # the emphasised word wins
    for b in inserts:
        assert b.end - b.start <= 2.0 and b.start >= 0.5 - 0.15
    assert match_broll(words, [], total=12.0) == []


@requires_ffmpeg
def test_downloader_reuses_verified_file_and_rejects_corrupt(tmp_path: Path, monkeypatch) -> None:
    """Resume behaviour: an already-complete, verified file is reused without any network access."""
    from shortforge.core.errors import DownloadError
    from shortforge.engines.downloader.ytdlp_downloader import YtDlpDownloader

    good = tmp_path / "AAAAAAAAAAA.mkv"
    subprocess.run([ff.ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:d=3",
                    "-f", "lavfi", "-i", "sine=d=3", "-shortest", str(good)], check=True)
    import yt_dlp

    def boom(*a, **k):  # any network use would fail the test
        raise AssertionError("network used")

    monkeypatch.setattr(yt_dlp.YoutubeDL, "extract_info", boom)
    res = YtDlpDownloader().download("https://www.youtube.com/watch?v=AAAAAAAAAAA", tmp_path, "AAAAAAAAAAA",
                                     expected_duration=3.0)
    assert res.path == good and res.width == 640 and res.height == 360
    with pytest.raises(DownloadError):
        YtDlpDownloader.verify(good, expected_duration=120.0)  # truncated/wrong file is rejected
    bad = tmp_path / "broken.mkv"
    bad.write_bytes(b"not a video")
    with pytest.raises(DownloadError):
        YtDlpDownloader.verify(bad)
