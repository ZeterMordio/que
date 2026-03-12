"""Regression tests for the Phase 2 pipeline."""
from __future__ import annotations

import io
import time
from pathlib import Path
from threading import current_thread
from types import SimpleNamespace

from rich.console import Console

from que import pipeline
from que.cache import Cache


class FakeChecker:
    """Stub library checker for pipeline tests."""

    def __init__(self, in_library_urls: set[str]) -> None:
        self._in_library_urls = in_library_urls

    def is_in_library(self, artist: str, title: str):
        url = title.replace("Track ", "u")
        if url in self._in_library_urls:
            return SimpleNamespace(
                in_library=True,
                reason="matched existing library track",
            )
        return SimpleNamespace(
            in_library=False,
            reason="best score 0 < threshold 85",
        )


class FakeCache:
    """Simple in-memory cache stub."""

    def __init__(self, cached: dict[str, object] | None = None) -> None:
        self.cached = cached or {}
        self.set_calls: list[tuple[str, str]] = []
        self.set_threads: list[str] = []
        self.started_runs: list[dict[str, object]] = []
        self.run_items: list[dict[str, object]] = []
        self.finished_runs: list[dict[str, object]] = []

    def get(self, url: str):
        return self.cached.get(url)

    def set(self, url: str, title: str, artist: str, status: str) -> None:
        self.set_calls.append((url, status))
        self.set_threads.append(current_thread().name)

    def start_run(self, **kwargs) -> int | None:
        self.started_runs.append(kwargs)
        return 1

    def record_run_item(self, **kwargs) -> None:
        self.run_items.append(kwargs)

    def finish_run(self, **kwargs) -> None:
        self.finished_runs.append(kwargs)


def _meta(url: str) -> SimpleNamespace:
    """Build a fake TrackMeta-like object."""
    suffix = url[-1]
    return SimpleNamespace(
        url=url,
        artist=f"Artist {suffix}",
        title=f"Track {suffix}",
        raw_title=f"Artist {suffix} - Track {suffix}",
    )


def test_process_urls_parallel_downloads_commit_in_input_order(
    tmp_path, monkeypatch
) -> None:
    """Out-of-order downloads should still commit/cache in input order."""
    output = io.StringIO()
    console = Console(file=output, force_terminal=False, color_system=None)
    cache = FakeCache(
        cached={
            "u2": SimpleNamespace(
                status="downloaded",
                artist="Cached Artist",
                title="Cached Track",
            )
        }
    )
    config = SimpleNamespace(
        library_paths=[],
        fuzzy_threshold=85,
        staging_dir=tmp_path / "staging",
        audio_format="m4a",
        use_music_app=True,
        import_destination=tmp_path / "library",
    )
    staging_dirs: list[Path] = []

    monkeypatch.setattr(
        pipeline,
        "_build_checker",
        lambda config, console: FakeChecker({"u4"}),
    )
    monkeypatch.setattr(pipeline, "resolve_metadata", lambda url: _meta(url))

    def fake_download(
        url: str,
        staging_dir: Path,
        audio_format: str,
        ffmpeg_threads=None,
    ):
        staging_dirs.append(staging_dir)
        staging_dir.mkdir(parents=True, exist_ok=True)
        if url == "u1":
            time.sleep(0.05)
        elif url == "u3":
            time.sleep(0.01)
        elif url == "u5":
            return None
        path = staging_dir / f"{url}.m4a"
        path.write_text("audio", encoding="utf-8")
        return path

    monkeypatch.setattr(pipeline, "download_track", fake_download)
    monkeypatch.setattr(pipeline, "tag_file", lambda *args, **kwargs: True)

    def fake_import(
        file_path: Path,
        artist: str,
        library_path: Path,
        use_music_app: bool,
        playlist_name=None,
    ):
        if file_path.name == "u6.m4a":
            return False, "Import failed in Apple Music"
        return True, f"Imported via Apple Music -> {file_path.name}"

    monkeypatch.setattr(pipeline, "import_to_apple_music", fake_import)

    pipeline.process_urls(
        urls=["u1", "u2", "u3", "u4", "u5", "u6"],
        dry_run=False,
        config=config,
        cache=cache,
        jobs=3,
        console=console,
    )

    assert len(staging_dirs) == 4
    assert len(set(staging_dirs)) == 4
    assert all(path.parent.parent == config.staging_dir for path in staging_dirs)

    download_phase_calls = [
        (url, status)
        for url, status in cache.set_calls
        if url in {"u1", "u3", "u5", "u6"}
    ]
    assert download_phase_calls == [
        ("u1", "downloaded"),
        ("u3", "downloaded"),
        ("u5", "failed"),
        ("u6", "failed"),
    ]
    assert all(name == "MainThread" for name in cache.set_threads)
    assert cache.finished_runs[0]["download_failed_count"] == 1
    assert cache.finished_runs[0]["import_failed_count"] == 1
    assert cache.finished_runs[0]["downloaded_bytes"] == 15

    rendered = output.getvalue()
    assert "2 downloaded & imported" in rendered
    assert "1 already in library" in rendered
    assert "1 cached (skipped)" in rendered
    assert "2 failed" in rendered
    assert "download finished for Artist 3" in rendered
    assert "download failed for Artist 5" in rendered


def test_process_urls_dry_run_never_submits_downloads(tmp_path, monkeypatch) -> None:
    """Dry-run should stop before the parallel download stage."""
    output = io.StringIO()
    console = Console(file=output, force_terminal=False, color_system=None)
    cache = FakeCache()
    config = SimpleNamespace(
        library_paths=[],
        fuzzy_threshold=85,
        staging_dir=tmp_path / "staging",
        audio_format="m4a",
        use_music_app=True,
        import_destination=tmp_path / "library",
    )

    monkeypatch.setattr(
        pipeline,
        "_build_checker",
        lambda config, console: FakeChecker(set()),
    )
    monkeypatch.setattr(pipeline, "resolve_metadata", lambda url: _meta(url))
    monkeypatch.setattr(
        pipeline,
        "download_track",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("downloaded during dry-run")),
    )

    pipeline.process_urls(
        urls=["u1"],
        dry_run=True,
        config=config,
        cache=cache,
        jobs=3,
        console=console,
    )

    assert cache.set_calls == []
    assert "dry-run skipped" in output.getvalue()


def test_process_urls_persists_run_metrics_in_sqlite(tmp_path, monkeypatch) -> None:
    """Run and item timing metrics should be stored in the cache DB."""
    console = Console(file=io.StringIO(), force_terminal=False, color_system=None)
    cache = Cache(tmp_path / "cache.db")
    config = SimpleNamespace(
        library_paths=[],
        fuzzy_threshold=85,
        staging_dir=tmp_path / "staging",
        audio_format="m4a",
        use_music_app=True,
        import_destination=tmp_path / "library",
    )

    monkeypatch.setattr(
        pipeline,
        "_build_checker",
        lambda config, console: FakeChecker(set()),
    )
    monkeypatch.setattr(pipeline, "resolve_metadata", lambda url: _meta(url))

    def fake_download(
        url: str,
        staging_dir: Path,
        audio_format: str,
        ffmpeg_threads=None,
    ):
        staging_dir.mkdir(parents=True, exist_ok=True)
        path = staging_dir / f"{url}.m4a"
        path.write_text("audio", encoding="utf-8")
        return path

    monkeypatch.setattr(pipeline, "download_track", fake_download)
    monkeypatch.setattr(pipeline, "tag_file", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        pipeline,
        "import_to_apple_music",
        lambda file_path, artist, library_path, use_music_app, playlist_name=None: (
            True,
            f"Imported via Apple Music -> {file_path.name}",
        ),
    )

    try:
        pipeline.process_urls(
            urls=["u1", "u2"],
            dry_run=False,
            config=config,
            cache=cache,
            jobs=2,
            console=console,
        )

        run_row = cache.conn.execute(
            "SELECT run_mode, jobs, total_urls, queued_downloads, downloaded_count, "
            "downloaded_bytes, average_download_bytes_per_second, "
            "download_failed_count, import_failed_count, "
            "preflight_seconds, download_phase_seconds, total_seconds "
            "FROM processing_runs"
        ).fetchone()
        item_rows = cache.conn.execute(
            "SELECT item_index, url, status, queue_wait_seconds, download_seconds, "
            "tag_seconds, import_seconds, total_item_seconds, file_size_bytes, "
            "download_bytes_per_second, failure_stage, worker_name "
            "FROM processing_run_items ORDER BY item_index"
        ).fetchall()
    finally:
        cache.close()

    assert run_row is not None
    assert run_row[0] == "normal"
    assert run_row[1] == 2
    assert run_row[2] == 2
    assert run_row[3] == 2
    assert run_row[4] == 2
    assert run_row[5] == 10
    assert run_row[6] is not None
    assert run_row[7] == 0
    assert run_row[8] == 0
    assert run_row[9] is not None
    assert run_row[10] is not None
    assert run_row[11] is not None

    assert len(item_rows) == 2
    assert item_rows[0][1] == "u1"
    assert item_rows[0][2] == "downloaded"
    assert item_rows[0][3] is not None
    assert item_rows[0][4] is not None
    assert item_rows[0][5] is not None
    assert item_rows[0][6] is not None
    assert item_rows[0][7] is not None
    assert item_rows[0][8] == 5
    assert item_rows[0][9] is not None
    assert item_rows[0][10] == ""
    assert item_rows[0][11].startswith("ThreadPoolExecutor")


def test_process_urls_benchmark_mode_skips_library_import_and_cache(
    tmp_path, monkeypatch
) -> None:
    """Benchmark mode should stay repeatable and download-only."""
    output = io.StringIO()
    console = Console(file=output, force_terminal=False, color_system=None)
    cache = FakeCache(
        cached={
            "u1": SimpleNamespace(
                status="downloaded",
                artist="Cached Artist",
                title="Cached Track",
            )
        }
    )
    config = SimpleNamespace(
        library_paths=[],
        fuzzy_threshold=85,
        staging_dir=tmp_path / "staging",
        audio_format="m4a",
        use_music_app=True,
        import_destination=tmp_path / "library",
    )

    monkeypatch.setattr(
        pipeline,
        "_build_checker",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("library check used")),
    )
    monkeypatch.setattr(
        pipeline,
        "resolve_metadata",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("metadata used")),
    )

    def fake_download(
        url: str,
        staging_dir: Path,
        audio_format: str,
        ffmpeg_threads=None,
    ):
        staging_dir.mkdir(parents=True, exist_ok=True)
        path = staging_dir / f"{url}.m4a"
        path.write_text("audio", encoding="utf-8")
        return path

    monkeypatch.setattr(pipeline, "download_track", fake_download)
    monkeypatch.setattr(
        pipeline,
        "tag_file",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("tagging used")),
    )
    monkeypatch.setattr(
        pipeline,
        "import_to_apple_music",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("import used")),
    )

    pipeline.process_urls(
        urls=["u1", "u2"],
        dry_run=False,
        config=config,
        cache=cache,
        jobs=2,
        benchmark=True,
        console=console,
    )

    assert cache.started_runs[0]["run_mode"] == "benchmark"
    assert cache.set_calls == []
    assert cache.finished_runs[0]["downloaded_count"] == 2
    assert cache.finished_runs[0]["failed_count"] == 0
    assert [row["status"] for row in cache.run_items] == ["benchmarked", "benchmarked"]
    assert config.staging_dir.exists()
    assert list(config.staging_dir.iterdir()) == []

    rendered = output.getvalue()
    assert "Benchmark mode: skipping metadata, URL cache" in rendered
    assert "2 benchmark downloads" in rendered


def test_process_urls_jobs_one_uses_serial_flow(tmp_path, monkeypatch) -> None:
    """Single-worker normal mode should bypass the staged preflight pipeline."""
    output = io.StringIO()
    console = Console(file=output, force_terminal=False, color_system=None)
    cache = FakeCache()
    config = SimpleNamespace(
        library_paths=[],
        fuzzy_threshold=85,
        staging_dir=tmp_path / "staging",
        audio_format="m4a",
        use_music_app=True,
        import_destination=tmp_path / "library",
    )

    monkeypatch.setattr(
        pipeline,
        "_run_parallel_downloads",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("parallel path used")),
    )
    monkeypatch.setattr(pipeline, "resolve_metadata", lambda url: _meta(url))
    monkeypatch.setattr(
        pipeline,
        "_build_checker",
        lambda config, console: FakeChecker(set()),
    )

    def fake_download(
        url: str,
        staging_dir: Path,
        audio_format: str,
        ffmpeg_threads=None,
    ):
        assert ffmpeg_threads is None
        staging_dir.mkdir(parents=True, exist_ok=True)
        path = staging_dir / f"{url}.m4a"
        path.write_text("audio", encoding="utf-8")
        return path

    monkeypatch.setattr(pipeline, "download_track", fake_download)
    monkeypatch.setattr(pipeline, "tag_file", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        pipeline,
        "import_to_apple_music",
        lambda *args, **kwargs: (True, "Imported"),
    )

    pipeline.process_urls(
        urls=["u1"],
        dry_run=False,
        config=config,
        cache=cache,
        jobs=1,
        console=console,
    )

    rendered = output.getvalue()
    assert "Downloaded:" in rendered
    assert "Run 1 (normal): jobs=1" in rendered
