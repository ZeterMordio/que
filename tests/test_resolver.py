"""Regression tests for metadata resolution helpers."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

from que import resolver, yt_dlp_env


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
        resolver,
        "run_yt_dlp",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="NA|||Boards of Canada - Dayvan Cowboy|||Boards of Canada - Topic\n",
            stderr="",
        ),
    )

    meta = resolver.resolve_metadata("https://youtube.com/watch?v=abc")

    assert meta is not None
    assert meta.artist == "Boards of Canada"
    assert meta.title == "Dayvan Cowboy"
    assert meta.raw_title == "Boards of Canada - Dayvan Cowboy"


def test_resolve_metadata_lookup_retries_youtube_with_browser_cookies(monkeypatch) -> None:
    """YouTube anti-bot failures should retry metadata lookup with Chrome cookies."""
    calls: list[list[str]] = []
    responses = iter(
        [
            SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="ERROR: [youtube] abc: Sign in to confirm you’re not a bot.\n",
            ),
            SimpleNamespace(
                returncode=0,
                stdout="NA|||For Good|||Liquicity\n",
                stderr="",
            ),
        ]
    )

    def fake_run(command, *args, **kwargs):
        calls.append(list(command))
        return next(responses)

    monkeypatch.setattr(resolver, "run_yt_dlp", fake_run)

    lookup = resolver.resolve_metadata_lookup("https://youtu.be/PeudLhS3Wp8")

    assert lookup.meta is not None
    assert lookup.meta.artist == "Liquicity"
    assert lookup.meta.title == "For Good"
    assert lookup.used_browser_cookies is True
    assert "--cookies-from-browser" not in calls[0]
    assert "--cookies-from-browser" in calls[1]


def test_expand_playlist_falls_back_to_original_url_on_failure(monkeypatch) -> None:
    """Playlist expansion should degrade gracefully when yt-dlp fails."""
    monkeypatch.setattr(
        resolver,
        "run_yt_dlp",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=""),
    )

    url = "https://youtube.com/playlist?list=abc"
    expanded = resolver.expand_playlist(url)

    assert expanded == [url]


def test_is_playlist_url_treats_watch_urls_with_list_context_as_tracks() -> None:
    """Watch URLs should not become playlist jobs just because YouTube adds `list=`."""
    assert (
        resolver.is_playlist_url("https://www.youtube.com/watch?v=abc&list=RD123&index=28")
        is False
    )
    assert resolver.is_playlist_url("https://youtu.be/abc?list=RD123&index=28") is False
    assert resolver.is_playlist_url("https://www.youtube.com/playlist?list=PL123") is True


def test_yt_dlp_subprocess_env_strips_bundle_python_bootstrap(monkeypatch) -> None:
    """Bundled-app Python vars should not leak into child yt-dlp processes."""
    monkeypatch.setenv("PYTHONHOME", "/tmp/bad-home")
    monkeypatch.setenv("PYTHONPATH", "/tmp/bad-path")
    monkeypatch.setenv("PYTHONEXECUTABLE", "/tmp/python")
    monkeypatch.setenv("__PYVENV_LAUNCHER__", "/tmp/launcher")
    monkeypatch.setenv("PATH", "/usr/bin")

    env = yt_dlp_env.yt_dlp_subprocess_env()

    assert "PYTHONHOME" not in env
    assert "PYTHONPATH" not in env
    assert "PYTHONEXECUTABLE" not in env
    assert "__PYVENV_LAUNCHER__" not in env
    assert env["PATH"].split(os.pathsep)[:3] == [
        str(Path.home() / ".local" / "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
    ]
