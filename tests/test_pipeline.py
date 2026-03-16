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
from que.importer import ImportResult


class FakeChecker:
    """Stub library checker for pipeline tests."""

    def __init__(
        self,
        in_library_urls: set[str],
        *,
        strict_in_library_urls: set[str] | None = None,
    ) -> None:
        self._in_library_urls = in_library_urls
        self._strict_in_library_urls = (
            in_library_urls if strict_in_library_urls is None else strict_in_library_urls
        )

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

    def is_strictly_in_library(self, artist: str, title: str):
        url = title.replace("Track ", "u")
        if url in self._strict_in_library_urls:
            return SimpleNamespace(
                in_library=True,
                reason="exact current-library match",
            )
        return SimpleNamespace(
            in_library=False,
            reason="no exact current-library match",
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


def _lookup(url: str, *, error: str | None = None, used_browser_cookies: bool = False):
    """Build a fake metadata lookup payload for pipeline tests."""
    return SimpleNamespace(
        meta=None if error else _meta(url),
        error=error,
        used_browser_cookies=used_browser_cookies,
    )


def _import_result(
    message: str,
    *,
    success: bool = True,
    music_app_imported: bool = True,
    final_path: Path | None = None,
) -> ImportResult:
    """Build an importer-like result payload for pipeline tests."""
    return ImportResult(
        success=success,
        music_app_imported=music_app_imported,
        message=message,
        final_path=final_path,
    )


def test_metadata_failure_message_surfaces_youtube_bot_block() -> None:
    """Anti-bot metadata failures should get a more truthful user-facing label."""
    message = pipeline._metadata_failure_message(
        "ERROR: [youtube] abc: Sign in to confirm you’re not a bot."
    )

    assert message == "YouTube blocked metadata lookup — skipping"


def test_process_urls_does_not_short_circuit_cached_failed_entry(
    tmp_path,
    monkeypatch,
) -> None:
    """Failed cache entries should be informational only, not a durable skip."""
    output = io.StringIO()
    console = Console(file=output, force_terminal=False, color_system=None)
    cache = FakeCache(
        cached={
            "u1": SimpleNamespace(
                status="failed",
                artist="",
                title="",
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
        lambda config, console: FakeChecker(set()),
    )
    monkeypatch.setattr(pipeline, "resolve_metadata_lookup", lambda url: _lookup(url))
    monkeypatch.setattr(
        pipeline,
        "download_track",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("download should not run")),
    )

    pipeline.process_urls(
        urls=["u1"],
        dry_run=True,
        config=config,
        cache=cache,
        jobs=1,
        console=console,
    )

    rendered = output.getvalue()
    assert "retryable; continuing" in rendered
    assert "Cached previous result: failed" not in rendered


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
                artist="Artist 2",
                title="Track 2",
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
        lambda config, console: FakeChecker({"u2", "u4"}),
    )
    monkeypatch.setattr(pipeline, "resolve_metadata_lookup", lambda url: _lookup(url))

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
            return _import_result(
                "Import failed in Apple Music",
                success=False,
                music_app_imported=False,
            )
        return _import_result(
            f"Imported via Apple Music -> {file_path.name}",
            final_path=file_path,
        )

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
    monkeypatch.setattr(pipeline, "resolve_metadata_lookup", lambda url: _lookup(url))
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
    monkeypatch.setattr(pipeline, "resolve_metadata_lookup", lambda url: _lookup(url))

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
        lambda file_path, artist, library_path, use_music_app, playlist_name=None: _import_result(
            f"Imported via Apple Music -> {file_path.name}",
            final_path=file_path,
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
        "resolve_metadata_lookup",
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
    monkeypatch.setattr(pipeline, "resolve_metadata_lookup", lambda url: _lookup(url))
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
        lambda file_path, *args, **kwargs: _import_result(
            "Imported",
            final_path=file_path,
        ),
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


def test_process_urls_emits_structured_events(tmp_path, monkeypatch) -> None:
    """Daemon-facing event hooks should expose item, results, and summary events."""
    events: list[tuple[str, str | None, dict[str, object]]] = []
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
        lambda config, ctx: FakeChecker(set()),
    )
    monkeypatch.setattr(pipeline, "resolve_metadata_lookup", lambda url: _lookup(url))

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
        lambda file_path, *args, **kwargs: _import_result(
            "Imported",
            final_path=file_path,
        ),
    )

    pipeline.process_urls(
        urls=["u1"],
        dry_run=False,
        config=config,
        cache=cache,
        jobs=1,
        console=None,
        event_handler=lambda event_type, message, payload: events.append(
            (event_type, message, payload)
        ),
    )

    event_types = [event_type for event_type, _message, _payload in events]
    assert "run_started" in event_types
    assert "item_report" in event_types
    assert "results" in event_types
    assert "summary" in event_types


def test_process_urls_revalidates_stale_in_library_cache(tmp_path, monkeypatch) -> None:
    """Stale in_library cache entries should fall through to a real download."""
    output = io.StringIO()
    console = Console(file=output, force_terminal=False, color_system=None)
    cache = FakeCache(
        cached={
            "u1": SimpleNamespace(
                status="in_library",
                artist="Artist 1",
                title="Track 1",
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
        lambda config, ctx: FakeChecker(set()),
    )
    monkeypatch.setattr(pipeline, "resolve_metadata_lookup", lambda url: _lookup(url))

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
        lambda file_path, *args, **kwargs: _import_result(
            f"Imported via Apple Music -> {file_path.name}",
            final_path=file_path,
        ),
    )

    pipeline.process_urls(
        urls=["u1"],
        dry_run=False,
        config=config,
        cache=cache,
        jobs=1,
        console=console,
    )

    assert cache.set_calls == [("u1", "downloaded")]
    rendered = output.getvalue()
    assert "Cached in_library entry no longer verified" in rendered
    assert "1 downloaded & imported" in rendered


def test_process_urls_retries_existing_file_when_cached_download_missing_from_music(
    tmp_path, monkeypatch
) -> None:
    """Downloaded cache entries should retry Music.app import before redownloading."""
    output = io.StringIO()
    console = Console(file=output, force_terminal=False, color_system=None)
    cache = FakeCache(
        cached={
            "u1": SimpleNamespace(
                status="downloaded",
                artist="Artist 1",
                title="Track 1",
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
    existing_file = (
        config.import_destination / "Artist 1" / "Unknown Album" / "Track 1.m4a"
    )
    existing_file.parent.mkdir(parents=True, exist_ok=True)
    existing_file.write_text("audio", encoding="utf-8")

    monkeypatch.setattr(
        pipeline,
        "_build_checker",
        lambda config, ctx: FakeChecker(set()),
    )
    monkeypatch.setattr(
        pipeline,
        "resolve_metadata_lookup",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("metadata should not run")),
    )
    monkeypatch.setattr(
        pipeline,
        "download_track",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("download should not run")),
    )
    monkeypatch.setattr(
        pipeline,
        "retry_music_app_import",
        lambda *args, **kwargs: _import_result(
            f"Imported existing library file via Music.app -> {existing_file.name}",
            final_path=existing_file,
        ),
    )

    pipeline.process_urls(
        urls=["u1"],
        dry_run=False,
        config=config,
        cache=cache,
        jobs=1,
        console=console,
    )

    assert cache.set_calls == [("u1", "downloaded")]
    rendered = output.getvalue()
    assert "Imported existing library file via Music.app" in rendered
    assert "1 downloaded & imported" in rendered


def test_process_urls_does_not_skip_cached_download_on_fuzzy_only_library_match(
    tmp_path, monkeypatch
) -> None:
    """Cache verification should require an exact current-library match."""
    output = io.StringIO()
    console = Console(file=output, force_terminal=False, color_system=None)
    cache = FakeCache(
        cached={
            "u1": SimpleNamespace(
                status="downloaded",
                artist="Artist 1",
                title="Track 1",
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
    resolved_urls: list[str] = []

    monkeypatch.setattr(
        pipeline,
        "_build_checker",
        lambda config, ctx: FakeChecker({"u1"}, strict_in_library_urls=set()),
    )

    def fake_resolve(url: str):
        resolved_urls.append(url)
        return _lookup(url)

    monkeypatch.setattr(pipeline, "resolve_metadata_lookup", fake_resolve)
    monkeypatch.setattr(
        pipeline,
        "retry_music_app_import",
        lambda *args, **kwargs: _import_result(
            "No existing library file found for retry",
            success=False,
            music_app_imported=False,
        ),
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
    monkeypatch.setattr(pipeline, "tag_file", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        pipeline,
        "import_to_apple_music",
        lambda file_path, *args, **kwargs: _import_result(
            f"Imported via Apple Music -> {file_path.name}",
            final_path=file_path,
        ),
    )

    pipeline.process_urls(
        urls=["u1"],
        dry_run=False,
        config=config,
        cache=cache,
        jobs=1,
        console=console,
    )

    assert resolved_urls == ["u1"]
    assert cache.set_calls == [("u1", "downloaded")]
    rendered = output.getvalue()
    assert "missing from current library state" in rendered
    assert "1 downloaded & imported" in rendered


def test_process_urls_marks_music_app_notification_failure_as_pending_import(
    tmp_path, monkeypatch
) -> None:
    """Move-only outcomes should stay retryable instead of claiming success."""
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
        lambda config, ctx: FakeChecker(set()),
    )
    monkeypatch.setattr(pipeline, "resolve_metadata_lookup", lambda url: _lookup(url))

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
        lambda file_path, *args, **kwargs: _import_result(
            f"In library folder (Music.app notification failed) -> {file_path.name}",
            music_app_imported=False,
            final_path=file_path,
        ),
    )

    pipeline.process_urls(
        urls=["u1"],
        dry_run=False,
        config=config,
        cache=cache,
        jobs=1,
        console=console,
    )

    assert cache.set_calls == [("u1", "pending_import")]
    rendered = output.getvalue()
    assert "Music.app notification failed" in rendered
    assert "1 failed" in rendered
