import pytest

from shortforge.engines.youtube.urls import SourceKind, SourceParseError, parse_source, uploads_playlist_id

CHANNEL_ID = "UCHnyfMqiRRG1u-2MsSQLbXA"


@pytest.mark.parametrize(
    ("text", "kind", "value"),
    [
        ("@ExampleChannel", SourceKind.HANDLE, "@ExampleChannel"),
        ("  @some.creator-01 ", SourceKind.HANDLE, "@some.creator-01"),
        ("youtube.com/@ExampleChannel", SourceKind.HANDLE, "@ExampleChannel"),
        ("https://www.youtube.com/@ExampleChannel/videos", SourceKind.HANDLE, "@ExampleChannel"),
        ("https://m.youtube.com/@ExampleChannel/shorts", SourceKind.HANDLE, "@ExampleChannel"),
        (f"https://www.youtube.com/channel/{CHANNEL_ID}", SourceKind.CHANNEL, CHANNEL_ID),
        (f"youtube.com/channel/{CHANNEL_ID}/featured", SourceKind.CHANNEL, CHANNEL_ID),
        (CHANNEL_ID, SourceKind.CHANNEL, CHANNEL_ID),
        ("https://www.youtube.com/c/SomeCustomName", SourceKind.CHANNEL, "c/SomeCustomName"),
        ("https://www.youtube.com/user/OldUsername", SourceKind.CHANNEL, "user/OldUsername"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", SourceKind.VIDEO, "dQw4w9WgXcQ"),
        ("youtube.com/watch?v=dQw4w9WgXcQ&t=42s", SourceKind.VIDEO, "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ?si=abc", SourceKind.VIDEO, "dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", SourceKind.VIDEO, "dQw4w9WgXcQ"),
        ("https://www.youtube.com/live/dQw4w9WgXcQ", SourceKind.VIDEO, "dQw4w9WgXcQ"),
        ("https://music.youtube.com/watch?v=dQw4w9WgXcQ", SourceKind.VIDEO, "dQw4w9WgXcQ"),
        ("https://www.youtube.com/playlist?list=PLabcdefghijklmnop", SourceKind.PLAYLIST, "PLabcdefghijklmnop"),
        ("PLabcdefghijklmnop123", SourceKind.PLAYLIST, "PLabcdefghijklmnop123"),
    ],
)
def test_parse_sources(text: str, kind: SourceKind, value: str) -> None:
    ref = parse_source(text)
    assert ref.kind == kind
    assert ref.value == value


def test_watch_url_keeps_playlist_hint_and_time() -> None:
    ref = parse_source("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLxyz1234567890&t=1m30s")
    assert ref.kind == SourceKind.VIDEO
    assert ref.playlist_id == "PLxyz1234567890"
    assert ref.start_time == 90.0


def test_canonical_urls() -> None:
    assert parse_source("@abc").url == "https://www.youtube.com/@abc"
    assert parse_source("https://youtu.be/dQw4w9WgXcQ").url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_local_file(tmp_path) -> None:
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"x")
    ref = parse_source(str(f))
    assert ref.kind == SourceKind.LOCAL_FILE
    assert ref.value == str(f.resolve())


@pytest.mark.parametrize("bad", ["", "   ", "https://vimeo.com/123", "hello world", "https://www.youtube.com/watch",
                                 "https://www.youtube.com/playlist", "@a", "C:/video/file.txt"])
def test_invalid_inputs(bad: str) -> None:
    with pytest.raises(SourceParseError):
        parse_source(bad)


def test_uploads_playlist() -> None:
    assert uploads_playlist_id(CHANNEL_ID) == "UU" + CHANNEL_ID[2:]
    with pytest.raises(ValueError):
        uploads_playlist_id("nope")
