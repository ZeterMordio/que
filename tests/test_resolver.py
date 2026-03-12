"""Regression tests for metadata resolution helpers."""
from __future__ import annotations

from types import SimpleNamespace

from que import resolver


def test_parse_artist_title_prefers_artist_field() -> None:
    """A valid explicit artist field should win over title parsing."""
    artist, title = resolver._parse_artist_title(
        "Artist - Track (Official Video)",
        "Boards of Canada",
        "Uploader",
    )

    assert artist == "Boards of Canada"
    assert title == "Artist - Track"


def test_parse_artist_title_falls_back_to_split_title() -> None:
    """The cleaned video title should split into artist/title when needed."""
    artist, title = resolver._parse_artist_title(
        "Boards of Canada - Dayvan Cowboy (Official Video)",
        "",
        "",
    )

    assert artist == "Boards of Canada"
    assert title == "Dayvan Cowboy"


def test_parse_artist_title_falls_back_to_uploader_topic_suffix() -> None:
    """Uploader fallback should strip YouTube's '- Topic' suffix."""
    artist, title = resolver._parse_artist_title(
        "Dayvan Cowboy",
        "",
        "Boards of Canada - Topic",
    )

    assert artist == "Boards of Canada"
    assert title == "Dayvan Cowboy"


def test_resolve_metadata_parses_yt_dlp_output(monkeypatch) -> None:
    """The resolver should parse a successful yt-dlp response into TrackMeta."""
    monkeypatch.setattr(
        resolver.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="NA|||Boards of Canada - Dayvan Cowboy|||Boards of Canada - Topic\n",
        ),
    )

    meta = resolver.resolve_metadata("https://youtube.com/watch?v=abc")

    assert meta is not None
    assert meta.artist == "Boards of Canada"
    assert meta.title == "Dayvan Cowboy"
    assert meta.raw_title == "Boards of Canada - Dayvan Cowboy"


def test_expand_playlist_falls_back_to_original_url_on_failure(monkeypatch) -> None:
    """Playlist expansion should degrade gracefully when yt-dlp fails."""
    monkeypatch.setattr(
        resolver.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=""),
    )

    url = "https://youtube.com/playlist?list=abc"
    expanded = resolver.expand_playlist(url)

    assert expanded == [url]
