"""Regression tests for library indexing and matching."""
from __future__ import annotations

from pathlib import Path

from que import library


def test_normalize_strips_track_numbers_extensions_and_feat() -> None:
    """Normalization should remove common filename noise before matching."""
    assert (
        library._normalize("01 - Dayvan Cowboy (feat. Guest).m4a")
        == "dayvan cowboy"
    )


def test_index_one_path_handles_structured_and_flat_layouts(tmp_path) -> None:
    """Indexing should infer artist/album when present and tolerate flat roots."""
    structured = tmp_path / "Artist" / "Album"
    structured.mkdir(parents=True)
    (structured / "01 - Song.m4a").write_text("audio", encoding="utf-8")
    (tmp_path / "Loose Song.mp3").write_text("audio", encoding="utf-8")

    tracks = library._index_one_path(tmp_path)
    by_title = {track.title: track for track in tracks}

    assert by_title["song"].artist == "Artist"
    assert by_title["song"].album == "Album"
    assert by_title["loose song"].artist == ""
    assert by_title["loose song"].album == ""


def test_fuzzy_library_checker_matches_music_app_track(monkeypatch) -> None:
    """Music.app index data should take precedence when available."""
    music_track = library.Track(
        path=Path("/"),
        artist="Boards of Canada",
        album="",
        title="dayvan cowboy",
    )

    monkeypatch.setattr(library, "build_music_app_index", lambda: [music_track])
    monkeypatch.setattr(
        library,
        "build_library_index",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("filesystem used")),
    )

    checker = library.FuzzyLibraryChecker(Path("/tmp"))
    result = checker.is_in_library("Boards of Canada", "Dayvan Cowboy")

    assert checker._source == "Music.app"
    assert result.in_library is True
    assert result.matched_track == music_track


def test_fuzzy_library_checker_title_only_match_stays_below_default_threshold(
    monkeypatch,
) -> None:
    """Title-only matching should stay conservative by default."""
    monkeypatch.setattr(library, "build_music_app_index", lambda: [])
    monkeypatch.setattr(
        library,
        "build_library_index",
        lambda *args, **kwargs: [
            library.Track(
                path=Path("/tmp/song.m4a"),
                artist="",
                album="",
                title="dayvan cowboy",
            )
        ],
    )

    checker = library.FuzzyLibraryChecker(Path("/tmp"), threshold=85)
    result = checker.is_in_library("", "Dayvan Cowboy")

    assert result.in_library is False
    assert result.confidence == 70.0
    assert result.matched_track is None
    assert "will download" in result.reason
