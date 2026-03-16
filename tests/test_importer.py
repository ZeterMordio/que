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

    result = importer.import_to_apple_music(
        source,
        artist="Boards of Canada",
        library_path=library_root,
        playlist_name="Road Trip",
    )

    assert result.success is True
    assert result.music_app_imported is True
    assert 'added to "Road Trip"' in result.message
    assert 'user playlist "Road Trip"' in captured["script"]
    assert not source.exists()
    assert (library_root / "Boards of Canada" / "Unknown Album" / "song.m4a").exists()


def test_import_to_apple_music_returns_retryable_result_when_music_app_fails(
    tmp_path, monkeypatch
) -> None:
    """Move-only outcomes should stay explicit so the pipeline can retry later."""
    source = tmp_path / "song.m4a"
    source.write_text("audio", encoding="utf-8")
    library_root = tmp_path / "library"

    monkeypatch.setattr(
        importer.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1),
    )

    result = importer.import_to_apple_music(
        source,
        artist="Boards of Canada",
        library_path=library_root,
    )

    assert result.success is True
    assert result.music_app_imported is False
    assert "Music.app notification failed" in result.message
    assert result.final_path is not None
    assert result.final_path.exists()


def test_retry_music_app_import_reuses_existing_library_file(tmp_path, monkeypatch) -> None:
    """Existing moved files should be importable without a redownload."""
    library_root = tmp_path / "library"
    existing = library_root / "Boards of Canada" / "Unknown Album" / "02 Olson.m4a"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text("audio", encoding="utf-8")

    monkeypatch.setattr(
        importer.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )

    result = importer.retry_music_app_import(
        library_root,
        artist="Boards of Canada",
        title="Olson",
    )

    assert result.success is True
    assert result.music_app_imported is True
    assert result.final_path == existing
