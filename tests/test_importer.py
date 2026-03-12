"""Regression tests for Apple Music import behavior."""
from __future__ import annotations

from types import SimpleNamespace

from que import importer


def test_import_to_apple_music_adds_track_to_named_playlist(
    tmp_path, monkeypatch
) -> None:
    """Playlist imports should pass the playlist name through AppleScript."""
    source = tmp_path / "song.m4a"
    source.write_text("audio", encoding="utf-8")
    library_root = tmp_path / "library"
    captured: dict[str, str] = {}

    def fake_run(args, capture_output, text, timeout):
        captured["script"] = args[2]
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(importer.subprocess, "run", fake_run)

    ok, message = importer.import_to_apple_music(
        source,
        artist="Boards of Canada",
        library_path=library_root,
        playlist_name="Road Trip",
    )

    assert ok is True
    assert 'added to "Road Trip"' in message
    assert 'user playlist "Road Trip"' in captured["script"]
    assert not source.exists()
    assert (library_root / "Boards of Canada" / "Unknown Album" / "song.m4a").exists()
