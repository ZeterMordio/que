"""Regression tests for main CLI entry points."""
from __future__ import annotations

import io
from datetime import datetime
from types import SimpleNamespace

import pytest
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

    def fake_process_urls(
        urls,
        dry_run,
        config,
        cache,
        playlist_name=None,
        jobs=3,
        benchmark=False,
        console=None,
    ):
        calls["urls"] = urls
        calls["dry_run"] = dry_run
        calls["use_music_app"] = config.use_music_app
        calls["playlist_name"] = playlist_name
        calls["jobs"] = jobs

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
    assert calls["jobs"] == 3
    assert "Playlist import requires Music.app integration" in output.getvalue()


def test_main_jobs_flag_is_passed_to_process_urls(monkeypatch, tmp_path) -> None:
    """The CLI should forward `--jobs` to the processing pipeline."""
    calls: dict[str, object] = {}
    config = SimpleNamespace(
        library_paths=[],
        fuzzy_threshold=85,
        staging_dir=tmp_path / "staging",
        audio_format="m4a",
        use_music_app=True,
        import_mode="move_then_music",
        import_destination=tmp_path / "library",
        cache_path=tmp_path / "cache.db",
    )

    def fake_process_urls(
        urls,
        dry_run,
        config,
        cache,
        playlist_name=None,
        jobs=3,
        benchmark=False,
        console=None,
    ):
        calls["jobs"] = jobs

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
        ["que", "--jobs", "5", "--no-cache", "https://youtube.com/watch?v=abc"],
    )

    main.main()

    assert calls["jobs"] == 5


def test_main_rejects_invalid_jobs_value(monkeypatch) -> None:
    """`--jobs` must reject values below 1."""
    monkeypatch.setattr(
        main.sys,
        "argv",
        ["que", "--jobs", "0", "https://youtube.com/watch?v=abc"],
    )

    with pytest.raises(SystemExit) as exc_info:
        main.main()

    assert exc_info.value.code == 2


def test_main_help_mentions_download_engine_benchmark(monkeypatch, capsys) -> None:
    """Help text should describe benchmark mode as download-engine-only."""
    monkeypatch.setattr(main.sys, "argv", ["que", "--help"])

    with pytest.raises(SystemExit) as exc_info:
        main.main()

    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "Benchmark download-engine throughput" in help_text
    assert "Benchmark the download engine only" in help_text


def test_main_benchmark_mode_passes_flag_and_uses_metrics_cache(
    monkeypatch, tmp_path
) -> None:
    """Benchmark mode should bypass `_NullCache` and pass the flag through."""
    calls: dict[str, object] = {}
    config = SimpleNamespace(
        library_paths=[],
        fuzzy_threshold=85,
        staging_dir=tmp_path / "staging",
        audio_format="m4a",
        use_music_app=True,
        import_mode="move_then_music",
        import_destination=tmp_path / "library",
        cache_path=tmp_path / "cache.db",
    )
    cache_obj = SimpleNamespace(close=lambda: None)

    def fake_process_urls(
        urls,
        dry_run,
        config,
        cache,
        playlist_name=None,
        jobs=3,
        benchmark=False,
        console=None,
    ):
        calls["cache"] = cache
        calls["benchmark"] = benchmark

    monkeypatch.setattr(main, "load_config", lambda: config)
    monkeypatch.setattr(main, "process_urls", fake_process_urls)
    monkeypatch.setattr(main, "Cache", lambda path: cache_obj)
    monkeypatch.setattr(
        main,
        "_NullCache",
        lambda: (_ for _ in ()).throw(AssertionError("_NullCache should not be used")),
    )
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
            "--benchmark",
            "--no-cache",
            "https://youtube.com/watch?v=abc",
        ],
    )

    main.main()

    assert calls["cache"] is cache_obj
    assert calls["benchmark"] is True


def test_main_rejects_benchmark_playlist_combination(monkeypatch) -> None:
    """Benchmark mode should reject incompatible playlist imports."""
    monkeypatch.setattr(
        main.sys,
        "argv",
        [
            "que",
            "--benchmark",
            "--playlist",
            "Road Trip",
            "https://youtube.com/watch?v=abc",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main.main()

    assert exc_info.value.code == 2


def test_main_runs_command_renders_recent_metrics(monkeypatch, tmp_path) -> None:
    """`que runs` should print recent aggregate performance metrics."""
    output = io.StringIO()
    config = SimpleNamespace(cache_path=tmp_path / "cache.db")
    rows = [
        SimpleNamespace(
            run_id=7,
            run_mode="benchmark",
            started_at=datetime(2026, 3, 12, 14, 5),
            jobs=3,
            total_urls=42,
            downloaded_count=30,
            downloaded_bytes=30 * 1024 * 1024,
            failed_count=2,
            download_failed_count=1,
            import_failed_count=1,
            total_seconds=88.2,
            average_download_bytes_per_second=1.5 * 1024 * 1024,
        )
    ]

    class FakeRunsCache:
        def recent_runs(self, limit=20):
            return rows

        def close(self):
            return None

    monkeypatch.setattr(
        main,
        "console",
        Console(file=output, force_terminal=False, color_system=None),
    )
    monkeypatch.setattr(main, "load_config", lambda: config)
    monkeypatch.setattr(main, "Cache", lambda path: FakeRunsCache())
    monkeypatch.setattr(main.sys, "argv", ["que", "runs", "--limit", "5"])

    main.main()

    rendered = output.getvalue()
    assert "que runs" in rendered
    assert "benchmark" in rendered
    assert "30.0 MiB" in rendered
    assert "1.5 MiB/s" in rendered
    assert "2 (d:1/i:1)" in rendered
