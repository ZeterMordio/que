"""Regression tests for main CLI entry points."""
from __future__ import annotations

import io
from types import SimpleNamespace

from rich.console import Console

from que import main


def test_main_playlist_flag_forces_music_app_and_passes_playlist_name(
    monkeypatch, tmp_path
) -> None:
    """Playlist imports should force Music.app integration for the run."""
    output = io.StringIO()
    calls: dict[str, object] = {}

    config = SimpleNamespace(
        library_paths=[],
        fuzzy_threshold=85,
        staging_dir=tmp_path / "staging",
        audio_format="m4a",
        use_music_app=False,
        import_mode="move_then_music",
        import_destination=tmp_path / "library",
        cache_path=tmp_path / "cache.db",
    )

    def fake_process_urls(urls, dry_run, config, cache, playlist_name=None):
        calls["urls"] = urls
        calls["dry_run"] = dry_run
        calls["use_music_app"] = config.use_music_app
        calls["playlist_name"] = playlist_name

    monkeypatch.setattr(
        main,
        "console",
        Console(file=output, force_terminal=False, color_system=None),
    )
    monkeypatch.setattr(main, "load_config", lambda: config)
    monkeypatch.setattr(main, "process_urls", fake_process_urls)
    monkeypatch.setattr(main, "parse_urls", lambda value: [value])
    monkeypatch.setattr(main, "normalize_url", lambda value: value)
    monkeypatch.setattr(main, "is_playlist_url", lambda value: False)
    monkeypatch.setattr(main, "expand_playlist", lambda value: [value])
    monkeypatch.setattr(main, "get_urls_from_clipboard", lambda: [])

    monkeypatch.setattr(
        main.sys,
        "argv",
        [
            "que",
            "--no-cache",
            "--playlist",
            "Road Trip",
            "https://youtube.com/watch?v=abc",
        ],
    )

    main.main()

    assert calls["urls"] == ["https://youtube.com/watch?v=abc"]
    assert calls["dry_run"] is False
    assert calls["use_music_app"] is True
    assert calls["playlist_name"] == "Road Trip"
    assert "Playlist import requires Music.app integration" in output.getvalue()
