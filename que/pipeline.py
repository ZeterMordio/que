"""
pipeline.py — staged processing pipeline for que.

Phase 2 keeps metadata resolution and library matching sequential, parallelizes
download work only, then serializes tagging/import/cache writes in the main
thread to avoid race-prone Apple Music and SQLite interactions.
"""
from __future__ import annotations

import shutil
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import current_thread
from time import perf_counter, sleep
from typing import Any

from rich.console import Console
from rich.table import Table

from .downloader import DownloadAttempt, download_track
from .importer import import_to_apple_music
from .library import FuzzyLibraryChecker
from .resolver import TrackMeta, resolve_metadata
from .tagger import tag_file

WORKER_START_STAGGER_SECONDS = 0.35


@dataclass
class TrackReport:
    """Final ordered result for a processed URL plus performance metadata."""

    index: int
    url: str
    status: str
    artist: str
    title: str
    note: str
    started_at: str | None = None
    queued_at: str | None = None
    download_started_at: str | None = None
    download_finished_at: str | None = None
    committed_at: str | None = None
    queue_wait_seconds: float | None = None
    download_seconds: float | None = None
    tag_seconds: float | None = None
    import_seconds: float | None = None
    total_item_seconds: float | None = None
    file_size_bytes: int | None = None
    download_bytes_per_second: float | None = None
    failure_stage: str = ""
    worker_name: str = ""


@dataclass
class DownloadTask:
    """Download task that survived preflight."""

    index: int
    url: str
    meta: TrackMeta
    staging_dir: Path
    item_started_at: str
    item_started_perf: float
    queued_at: str
    queued_perf: float
    ffmpeg_threads: int | None = None
    startup_delay_seconds: float = 0.0


@dataclass
class DownloadResult:
    """Outcome returned from a download worker."""

    index: int
    url: str
    meta: TrackMeta
    staging_dir: Path
    downloaded_path: Path | None
    item_started_at: str
    item_started_perf: float
    queued_at: str
    started_at: str | None = None
    finished_at: str | None = None
    queue_wait_seconds: float | None = None
    download_seconds: float | None = None
    file_size_bytes: int | None = None
    worker_name: str = ""
    failure_stage: str = ""
    error: str | None = None


def _now_iso() -> str:
    """Return the current local timestamp as ISO text."""
    return datetime.now().isoformat()


def _status_color(status: str) -> str:
    return {
        "downloaded": "green",
        "benchmarked": "green",
        "in_library": "cyan",
        "failed": "red",
        "skipped": "yellow",
        "cached": "magenta",
    }.get(status, "white")


def _label(artist: str, title: str) -> str:
    if artist:
        return f"[bold]{artist}[/bold] – {title}"
    return f"[bold]{title}[/bold]"


def _plain_label(artist: str, title: str) -> str:
    if artist:
        return f"{artist} - {title}"
    return title or "—"


def _build_checker(config: Any, console: Console) -> FuzzyLibraryChecker:
    """Construct and announce the library checker state."""
    checker = FuzzyLibraryChecker(
        library_paths=config.library_paths,
        threshold=config.fuzzy_threshold,
    )

    existing = [path for path in config.library_paths if path.exists()]
    if not existing:
        paths_str = "\n     ".join(str(path) for path in config.library_paths)
        console.print(
            f"[yellow]⚠  No library paths found. Checked:[/yellow]\n     {paths_str}\n"
            f"   Add your music folder to [bold]~/.config/que/config.toml[/bold]:\n"
            f'   [dim][[library]]\n   paths = ["~/Music/your-folder"][/dim]'
        )
    elif not checker._tracks:
        paths_str = ", ".join(str(path) for path in existing)
        console.print(
            "[yellow]⚠  Library paths exist but no audio files found:[/yellow] "
            f"[dim]{paths_str}[/dim]"
        )
    else:
        source_label = f"via {checker._source}"
        if checker._source == "filesystem":
            source_label += f" ({', '.join(str(path) for path in existing)})"
        console.print(f"[dim]📚 Library: {len(checker._tracks)} tracks {source_label}[/dim]")

    return checker


def _safe_rate(file_size_bytes: int | None, duration_seconds: float | None) -> float | None:
    """Return bytes/sec when both inputs are usable."""
    if file_size_bytes is None or duration_seconds is None or duration_seconds <= 0:
        return None
    return file_size_bytes / duration_seconds


def _make_report(
    index: int,
    url: str,
    status: str,
    artist: str,
    title: str,
    note: str,
    *,
    started_at: str | None = None,
    queued_at: str | None = None,
    download_started_at: str | None = None,
    download_finished_at: str | None = None,
    committed_at: str | None = None,
    queue_wait_seconds: float | None = None,
    download_seconds: float | None = None,
    tag_seconds: float | None = None,
    import_seconds: float | None = None,
    total_item_seconds: float | None = None,
    file_size_bytes: int | None = None,
    download_bytes_per_second: float | None = None,
    failure_stage: str = "",
    worker_name: str = "",
) -> TrackReport:
    """Build a final report entry."""
    return TrackReport(
        index=index,
        url=url,
        status=status,
        artist=artist,
        title=title,
        note=note,
        started_at=started_at,
        queued_at=queued_at,
        download_started_at=download_started_at,
        download_finished_at=download_finished_at,
        committed_at=committed_at,
        queue_wait_seconds=queue_wait_seconds,
        download_seconds=download_seconds,
        tag_seconds=tag_seconds,
        import_seconds=import_seconds,
        total_item_seconds=total_item_seconds,
        file_size_bytes=file_size_bytes,
        download_bytes_per_second=download_bytes_per_second,
        failure_stage=failure_stage,
        worker_name=worker_name,
    )


def _preflight(
    urls: list[str],
    dry_run: bool,
    benchmark: bool,
    jobs: int,
    config: Any,
    cache: Any,
    console: Console,
) -> tuple[list[DownloadTask], dict[int, TrackReport], dict[str, int]]:
    """Run metadata/cache/library checks in input order."""
    checker = None if benchmark else _build_checker(config, console)
    run_root = config.staging_dir / f"run-{uuid.uuid4().hex[:8]}"
    reports: dict[int, TrackReport] = {}
    download_tasks: list[DownloadTask] = []
    stats = {
        "downloaded": 0,
        "benchmarked": 0,
        "in_library": 0,
        "failed": 0,
        "cached": 0,
        "skipped": 0,
    }

    for index, url in enumerate(urls, 1):
        item_started_at = _now_iso()
        item_started_perf = perf_counter()
        console.print(f"\n[dim]({index}/{len(urls)})[/dim] [dim]{url}[/dim]")

        cached = None if benchmark else cache.get(url)
        if cached:
            console.print(
                f"  ⏭  [dim]Cached ({cached.status}):[/dim] "
                f"{_label(cached.artist, cached.title)}"
            )
            stats["cached"] += 1
            reports[index] = _make_report(
                index=index,
                url=url,
                status="cached",
                artist=cached.artist,
                title=cached.title,
                note=f"Cached previous result: {cached.status}",
                started_at=item_started_at,
                committed_at=_now_iso(),
                total_item_seconds=perf_counter() - item_started_perf,
            )
            continue

        if benchmark:
            meta = TrackMeta(
                url=url,
                title=url,
                artist="",
                raw_title=url,
            )
        else:
            with console.status("  Fetching metadata…", spinner="dots"):
                meta = resolve_metadata(url)

        if not meta:
            message = "Could not fetch metadata — skipping"
            console.print(f"  [red]✗  {message}[/red]")
            if not benchmark:
                cache.set(url, "", "", "failed")
            stats["failed"] += 1
            reports[index] = _make_report(
                index=index,
                url=url,
                status="failed",
                artist="",
                title="",
                note=message,
                started_at=item_started_at,
                committed_at=_now_iso(),
                total_item_seconds=perf_counter() - item_started_perf,
                failure_stage="metadata",
            )
            continue

        console.print(f"  🎵 {_label(meta.artist, meta.title)}")
        if meta.raw_title != meta.title:
            console.print(f"     [dim]raw: {meta.raw_title}[/dim]")

        if not benchmark and checker is not None:
            result = checker.is_in_library(meta.artist, meta.title)
            if result.in_library:
                note = f"Already in library — {result.reason}"
                console.print(
                    f"  [cyan]✓  Already in library[/cyan]  [dim]{result.reason}[/dim]"
                )
                cache.set(url, meta.title, meta.artist, "in_library")
                stats["in_library"] += 1
                reports[index] = _make_report(
                    index=index,
                    url=url,
                    status="in_library",
                    artist=meta.artist,
                    title=meta.title,
                    note=note,
                    started_at=item_started_at,
                    committed_at=_now_iso(),
                    total_item_seconds=perf_counter() - item_started_perf,
                )
                continue

            console.print(f"  [yellow]↓  Not in library[/yellow]  [dim]{result.reason}[/dim]")
        elif benchmark:
            console.print("  [blue]≈  Benchmark mode[/blue]  [dim]cache/library bypassed[/dim]")

        if dry_run:
            console.print("  [dim][dry-run] Would download[/dim]")
            stats["skipped"] += 1
            reports[index] = _make_report(
                index=index,
                url=url,
                status="skipped",
                artist=meta.artist,
                title=meta.title,
                note="Dry-run: would download",
                started_at=item_started_at,
                committed_at=_now_iso(),
                total_item_seconds=perf_counter() - item_started_perf,
            )
            continue

        queued_at = _now_iso()
        queued_perf = perf_counter()
        staging_dir = run_root / f"{index:04d}"
        console.print(
            f"  [blue]→  Queued for {'benchmark ' if benchmark else ''}download[/blue]  "
            f"[dim]job {index}/{len(urls)}[/dim]"
        )
        download_tasks.append(
            DownloadTask(
                index=index,
                url=url,
                meta=meta,
                staging_dir=staging_dir,
                item_started_at=item_started_at,
                item_started_perf=item_started_perf,
                queued_at=queued_at,
                queued_perf=queued_perf,
                ffmpeg_threads=1 if jobs > 1 else None,
            )
        )

    return download_tasks, reports, stats


def _download_worker(task: DownloadTask, audio_format: str) -> DownloadResult:
    """Download a single task in a worker thread."""
    if task.startup_delay_seconds > 0:
        sleep(task.startup_delay_seconds)

    started_at = _now_iso()
    started_perf = perf_counter()
    worker_name = current_thread().name
    attempt = download_track(
        task.url,
        task.staging_dir,
        audio_format,
        ffmpeg_threads=task.ffmpeg_threads,
    )
    finished_at = _now_iso()
    finished_perf = perf_counter()

    if isinstance(attempt, DownloadAttempt):
        downloaded_path = attempt.path
        error = attempt.error
    else:
        downloaded_path = attempt
        error = None if attempt else "Download failed"

    file_size_bytes = None
    if downloaded_path and downloaded_path.exists():
        file_size_bytes = downloaded_path.stat().st_size

    return DownloadResult(
        index=task.index,
        url=task.url,
        meta=task.meta,
        staging_dir=task.staging_dir,
        downloaded_path=downloaded_path,
        item_started_at=task.item_started_at,
        item_started_perf=task.item_started_perf,
        queued_at=task.queued_at,
        started_at=started_at,
        finished_at=finished_at,
        queue_wait_seconds=max(0.0, started_perf - task.queued_perf),
        download_seconds=max(0.0, finished_perf - started_perf),
        file_size_bytes=file_size_bytes,
        worker_name=worker_name,
        failure_stage="" if downloaded_path else "download",
        error=error or ("Download failed" if not downloaded_path else None),
    )


def _cleanup_staging_dir(staging_dir: Path) -> None:
    """Best-effort cleanup for per-job staging directories."""
    try:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
    except Exception:
        pass


def _cleanup_run_root(download_tasks: list[DownloadTask]) -> None:
    """Best-effort cleanup for the per-run staging root."""
    if not download_tasks:
        return
    run_root = download_tasks[0].staging_dir.parent
    try:
        if run_root.exists():
            shutil.rmtree(run_root)
    except Exception:
        pass


def _process_urls_serial(
    urls: list[str],
    dry_run: bool,
    config: Any,
    cache: Any,
    console: Console,
    playlist_name: str | None,
) -> tuple[dict[int, TrackReport], dict[str, int]]:
    """Process URLs with the old serial flow for responsive single-worker runs."""
    checker = _build_checker(config, console)
    reports: dict[int, TrackReport] = {}
    stats = {
        "downloaded": 0,
        "benchmarked": 0,
        "in_library": 0,
        "failed": 0,
        "cached": 0,
        "skipped": 0,
    }

    for index, url in enumerate(urls, 1):
        item_started_at = _now_iso()
        item_started_perf = perf_counter()
        console.print(f"\n[dim]({index}/{len(urls)})[/dim] [dim]{url}[/dim]")

        cached = cache.get(url)
        if cached:
            console.print(
                f"  ⏭  [dim]Cached ({cached.status}):[/dim] "
                f"{_label(cached.artist, cached.title)}"
            )
            stats["cached"] += 1
            reports[index] = _make_report(
                index=index,
                url=url,
                status="cached",
                artist=cached.artist,
                title=cached.title,
                note=f"Cached previous result: {cached.status}",
                started_at=item_started_at,
                committed_at=_now_iso(),
                total_item_seconds=perf_counter() - item_started_perf,
            )
            continue

        with console.status("  Fetching metadata…", spinner="dots"):
            meta = resolve_metadata(url)

        if not meta:
            message = "Could not fetch metadata — skipping"
            console.print(f"  [red]✗  {message}[/red]")
            cache.set(url, "", "", "failed")
            stats["failed"] += 1
            reports[index] = _make_report(
                index=index,
                url=url,
                status="failed",
                artist="",
                title="",
                note=message,
                started_at=item_started_at,
                committed_at=_now_iso(),
                total_item_seconds=perf_counter() - item_started_perf,
                failure_stage="metadata",
            )
            continue

        console.print(f"  🎵 {_label(meta.artist, meta.title)}")
        if meta.raw_title != meta.title:
            console.print(f"     [dim]raw: {meta.raw_title}[/dim]")

        result = checker.is_in_library(meta.artist, meta.title)
        if result.in_library:
            note = f"Already in library — {result.reason}"
            console.print(f"  [cyan]✓  Already in library[/cyan]  [dim]{result.reason}[/dim]")
            cache.set(url, meta.title, meta.artist, "in_library")
            stats["in_library"] += 1
            reports[index] = _make_report(
                index=index,
                url=url,
                status="in_library",
                artist=meta.artist,
                title=meta.title,
                note=note,
                started_at=item_started_at,
                committed_at=_now_iso(),
                total_item_seconds=perf_counter() - item_started_perf,
            )
            continue

        console.print(f"  [yellow]↓  Not in library[/yellow]  [dim]{result.reason}[/dim]")
        if dry_run:
            console.print("  [dim][dry-run] Would download[/dim]")
            stats["skipped"] += 1
            reports[index] = _make_report(
                index=index,
                url=url,
                status="skipped",
                artist=meta.artist,
                title=meta.title,
                note="Dry-run: would download",
                started_at=item_started_at,
                committed_at=_now_iso(),
                total_item_seconds=perf_counter() - item_started_perf,
            )
            continue

        download_started_at = _now_iso()
        download_start = perf_counter()
        with console.status("  Downloading…", spinner="dots"):
            attempt = download_track(
                url,
                config.staging_dir,
                config.audio_format,
                ffmpeg_threads=None,
            )
        download_finished_at = _now_iso()
        download_seconds = perf_counter() - download_start

        if isinstance(attempt, DownloadAttempt):
            downloaded_path = attempt.path
            error = attempt.error
        else:
            downloaded_path = attempt
            error = None if attempt else "Download failed"

        file_size_bytes = None
        if downloaded_path and downloaded_path.exists():
            file_size_bytes = downloaded_path.stat().st_size

        if not downloaded_path:
            cache.set(url, meta.title, meta.artist, "failed")
            stats["failed"] += 1
            reports[index] = _make_report(
                index=index,
                url=url,
                status="failed",
                artist=meta.artist,
                title=meta.title,
                note=error or "Download failed",
                started_at=item_started_at,
                download_started_at=download_started_at,
                download_finished_at=download_finished_at,
                committed_at=_now_iso(),
                download_seconds=download_seconds,
                total_item_seconds=perf_counter() - item_started_perf,
                file_size_bytes=file_size_bytes,
                download_bytes_per_second=_safe_rate(file_size_bytes, download_seconds),
                failure_stage="download",
            )
            continue

        console.print(f"  [green]✓  Downloaded:[/green] {downloaded_path.name}")

        tag_start = perf_counter()
        tagged = tag_file(downloaded_path, meta.artist, meta.title)
        tag_seconds = perf_counter() - tag_start
        if tagged:
            console.print("  🏷  Tagged with artist / title metadata")

        import_start = perf_counter()
        ok, message = import_to_apple_music(
            downloaded_path,
            meta.artist,
            config.import_destination,
            config.use_music_app,
            playlist_name=playlist_name,
        )
        import_seconds = perf_counter() - import_start
        committed_at = _now_iso()
        note = message if not tagged else f"{message}; tagged metadata"
        total_item_seconds = perf_counter() - item_started_perf

        if ok:
            console.print(f"  [green]✓  {message}[/green]")
            cache.set(url, meta.title, meta.artist, "downloaded")
            stats["downloaded"] += 1
            reports[index] = _make_report(
                index=index,
                url=url,
                status="downloaded",
                artist=meta.artist,
                title=meta.title,
                note=note,
                started_at=item_started_at,
                download_started_at=download_started_at,
                download_finished_at=download_finished_at,
                committed_at=committed_at,
                download_seconds=download_seconds,
                tag_seconds=tag_seconds,
                import_seconds=import_seconds,
                total_item_seconds=total_item_seconds,
                file_size_bytes=file_size_bytes,
                download_bytes_per_second=_safe_rate(file_size_bytes, download_seconds),
            )
            continue

        console.print(f"  [red]✗  {message}[/red]")
        cache.set(url, meta.title, meta.artist, "failed")
        stats["failed"] += 1
        reports[index] = _make_report(
            index=index,
            url=url,
            status="failed",
            artist=meta.artist,
            title=meta.title,
            note=message,
            started_at=item_started_at,
            download_started_at=download_started_at,
            download_finished_at=download_finished_at,
            committed_at=committed_at,
            download_seconds=download_seconds,
            tag_seconds=tag_seconds,
            import_seconds=import_seconds,
            total_item_seconds=total_item_seconds,
            file_size_bytes=file_size_bytes,
            download_bytes_per_second=_safe_rate(file_size_bytes, download_seconds),
            failure_stage="import",
        )

    return reports, stats


def _commit_download_result(
    result: DownloadResult,
    benchmark: bool,
    config: Any,
    cache: Any,
    playlist_name: str | None,
    stats: dict[str, int],
) -> TrackReport:
    """Tag/import/cache a completed download result in the main thread."""
    committed_at = _now_iso()
    if not result.downloaded_path:
        if not benchmark:
            cache.set(result.url, result.meta.title, result.meta.artist, "failed")
        stats["failed"] += 1
        _cleanup_staging_dir(result.staging_dir)
        return _make_report(
            index=result.index,
            url=result.url,
            status="failed",
            artist=result.meta.artist,
            title=result.meta.title,
            note=result.error or "Download failed",
            started_at=result.item_started_at,
            queued_at=result.queued_at,
            download_started_at=result.started_at,
            download_finished_at=result.finished_at,
            committed_at=committed_at,
            queue_wait_seconds=result.queue_wait_seconds,
            download_seconds=result.download_seconds,
            total_item_seconds=perf_counter() - result.item_started_perf,
            file_size_bytes=result.file_size_bytes,
            download_bytes_per_second=_safe_rate(
                result.file_size_bytes,
                result.download_seconds,
            ),
            failure_stage=result.failure_stage or "download",
            worker_name=result.worker_name,
        )

    if benchmark:
        stats["benchmarked"] += 1
        _cleanup_staging_dir(result.staging_dir)
        return _make_report(
            index=result.index,
            url=result.url,
            status="benchmarked",
            artist=result.meta.artist,
            title=result.meta.title,
            note="Benchmark download complete; throwaway staging cleaned up",
            started_at=result.item_started_at,
            queued_at=result.queued_at,
            download_started_at=result.started_at,
            download_finished_at=result.finished_at,
            committed_at=committed_at,
            queue_wait_seconds=result.queue_wait_seconds,
            download_seconds=result.download_seconds,
            total_item_seconds=perf_counter() - result.item_started_perf,
            file_size_bytes=result.file_size_bytes,
            download_bytes_per_second=_safe_rate(
                result.file_size_bytes,
                result.download_seconds,
            ),
            worker_name=result.worker_name,
        )

    tag_start = perf_counter()
    tagged = tag_file(result.downloaded_path, result.meta.artist, result.meta.title)
    tag_seconds = perf_counter() - tag_start

    import_start = perf_counter()
    ok, message = import_to_apple_music(
        result.downloaded_path,
        result.meta.artist,
        config.import_destination,
        config.use_music_app,
        playlist_name=playlist_name,
    )
    import_seconds = perf_counter() - import_start
    _cleanup_staging_dir(result.staging_dir)

    note = message
    if tagged:
        note = f"{message}; tagged metadata"

    total_item_seconds = perf_counter() - result.item_started_perf
    if ok:
        cache.set(result.url, result.meta.title, result.meta.artist, "downloaded")
        stats["downloaded"] += 1
        return _make_report(
            index=result.index,
            url=result.url,
            status="downloaded",
            artist=result.meta.artist,
            title=result.meta.title,
            note=note,
            started_at=result.item_started_at,
            queued_at=result.queued_at,
            download_started_at=result.started_at,
            download_finished_at=result.finished_at,
            committed_at=committed_at,
            queue_wait_seconds=result.queue_wait_seconds,
            download_seconds=result.download_seconds,
            tag_seconds=tag_seconds,
            import_seconds=import_seconds,
            total_item_seconds=total_item_seconds,
            file_size_bytes=result.file_size_bytes,
            download_bytes_per_second=_safe_rate(
                result.file_size_bytes,
                result.download_seconds,
            ),
            worker_name=result.worker_name,
        )

    cache.set(result.url, result.meta.title, result.meta.artist, "failed")
    stats["failed"] += 1
    return _make_report(
        index=result.index,
        url=result.url,
        status="failed",
        artist=result.meta.artist,
        title=result.meta.title,
        note=message,
        started_at=result.item_started_at,
        queued_at=result.queued_at,
        download_started_at=result.started_at,
        download_finished_at=result.finished_at,
        committed_at=committed_at,
        queue_wait_seconds=result.queue_wait_seconds,
        download_seconds=result.download_seconds,
        tag_seconds=tag_seconds,
        import_seconds=import_seconds,
        total_item_seconds=total_item_seconds,
        file_size_bytes=result.file_size_bytes,
        download_bytes_per_second=_safe_rate(
            result.file_size_bytes,
            result.download_seconds,
        ),
        failure_stage="import",
        worker_name=result.worker_name,
    )


def _run_parallel_downloads(
    download_tasks: list[DownloadTask],
    jobs: int,
    benchmark: bool,
    config: Any,
    cache: Any,
    console: Console,
    playlist_name: str | None,
    reports: dict[int, TrackReport],
    stats: dict[str, int],
) -> None:
    """Execute download workers and commit results serially in input order."""
    if not download_tasks:
        return

    def advance_committed_gaps(current_index: int) -> int:
        while current_index in reports:
            current_index += 1
        return current_index

    console.print(
        "\n[bold]Download Queue[/bold]  "
        f"[dim]{len(download_tasks)} track(s), {jobs} worker(s)[/dim]"
    )
    if jobs > 1 and len(download_tasks) > 1:
        console.print(
            "[dim]Gentle worker startup enabled to reduce initial CPU / I/O spikes.[/dim]"
        )

    pending_results: dict[int, DownloadResult] = {}
    next_commit_index = advance_committed_gaps(min(task.index for task in download_tasks))

    with ThreadPoolExecutor(max_workers=jobs) as executor:
        future_map: dict[Future[DownloadResult], DownloadTask] = {}
        for submit_index, task in enumerate(download_tasks):
            if submit_index < jobs:
                task.startup_delay_seconds = submit_index * WORKER_START_STAGGER_SECONDS
            future = executor.submit(_download_worker, task, config.audio_format)
            future_map[future] = task

        for future in as_completed(future_map):
            task = future_map[future]
            try:
                result = future.result()
            except Exception as exc:
                result = DownloadResult(
                    index=task.index,
                    url=task.url,
                    meta=task.meta,
                    staging_dir=task.staging_dir,
                    downloaded_path=None,
                    item_started_at=task.item_started_at,
                    item_started_perf=task.item_started_perf,
                    queued_at=task.queued_at,
                    error=f"Download worker crashed: {exc}",
                )

            if result.downloaded_path:
                console.print(
                    f"  [green]✓[/green] {'Benchmark ' if benchmark else ''}download "
                    f"finished for "
                    f"{_label(result.meta.artist, result.meta.title)}"
                )
            else:
                console.print(
                    f"  [red]✗[/red] {'Benchmark ' if benchmark else ''}download "
                    f"failed for "
                    f"{_label(result.meta.artist, result.meta.title)}"
                )

            pending_results[result.index] = result
            while next_commit_index in pending_results:
                committed = _commit_download_result(
                    pending_results.pop(next_commit_index),
                    benchmark=benchmark,
                    config=config,
                    cache=cache,
                    playlist_name=playlist_name,
                    stats=stats,
                )
                reports[next_commit_index] = committed
                next_commit_index += 1
                next_commit_index = advance_committed_gaps(next_commit_index)


def _render_results(urls: list[str], reports: dict[int, TrackReport], console: Console) -> None:
    """Render final ordered results in a stable table."""
    console.print()
    table = Table(title="que results", show_lines=False, highlight=True)
    table.add_column("#", style="dim", width=4)
    table.add_column("Status", style="bold", width=12)
    table.add_column("Track")
    table.add_column("Notes")

    for index in range(1, len(urls) + 1):
        report = reports[index]
        color = _status_color(report.status)
        table.add_row(
            str(index),
            f"[{color}]{report.status}[/{color}]",
            _plain_label(report.artist, report.title),
            report.note,
        )

    console.print(table)


def _persist_run_metrics(
    cache: Any,
    run_id: int | None,
    reports: dict[int, TrackReport],
    stats: dict[str, int],
    *,
    preflight_seconds: float,
    download_phase_seconds: float,
    total_seconds: float,
    queued_downloads: int,
) -> None:
    """Persist aggregate and per-item metrics into the SQLite cache DB."""
    if run_id is None:
        return

    for index in sorted(reports):
        report = reports[index]
        cache.record_run_item(
            run_id=run_id,
            item_index=report.index,
            url=report.url,
            artist=report.artist,
            title=report.title,
            status=report.status,
            note=report.note,
            started_at=report.started_at,
            queued_at=report.queued_at,
            download_started_at=report.download_started_at,
            download_finished_at=report.download_finished_at,
            committed_at=report.committed_at,
            queue_wait_seconds=report.queue_wait_seconds,
            download_seconds=report.download_seconds,
            tag_seconds=report.tag_seconds,
            import_seconds=report.import_seconds,
            total_item_seconds=report.total_item_seconds,
            file_size_bytes=report.file_size_bytes,
            download_bytes_per_second=report.download_bytes_per_second,
            failure_stage=report.failure_stage,
            worker_name=report.worker_name,
        )

    downloaded_bytes = sum(
        report.file_size_bytes or 0
        for report in reports.values()
        if report.file_size_bytes is not None and report.download_seconds is not None
    )
    total_download_seconds = sum(
        report.download_seconds or 0.0
        for report in reports.values()
        if report.file_size_bytes is not None and report.download_seconds is not None
    )
    download_failed_count = sum(
        1 for report in reports.values() if report.failure_stage == "download"
    )
    import_failed_count = sum(
        1 for report in reports.values() if report.failure_stage == "import"
    )
    cache.finish_run(
        run_id=run_id,
        preflight_seconds=preflight_seconds,
        download_phase_seconds=download_phase_seconds,
        total_seconds=total_seconds,
        queued_downloads=queued_downloads,
        downloaded_count=stats["downloaded"] + stats.get("benchmarked", 0),
        in_library_count=stats["in_library"],
        cached_count=stats["cached"],
        skipped_count=stats["skipped"],
        failed_count=stats["failed"],
        download_failed_count=download_failed_count,
        import_failed_count=import_failed_count,
        downloaded_bytes=downloaded_bytes,
        average_download_bytes_per_second=(
            downloaded_bytes / total_download_seconds
            if downloaded_bytes > 0 and total_download_seconds > 0
            else None
        ),
    )


def _print_summary(
    stats: dict[str, int],
    console: Console,
    *,
    run_id: int | None,
    run_mode: str,
    jobs: int,
    preflight_seconds: float,
    download_phase_seconds: float,
    total_seconds: float,
) -> None:
    """Render the summary section."""
    console.print()
    console.rule("[bold]Summary[/bold]")
    if run_mode == "benchmark":
        console.print(
            f"  [green]{stats['benchmarked']} benchmark downloads[/green]   "
            f"[red]{stats['failed']} failed[/red]"
        )
    else:
        console.print(
            f"  [green]{stats['downloaded']} downloaded & imported[/green]   "
            f"[cyan]{stats['in_library']} already in library[/cyan]   "
            f"[dim]{stats['cached']} cached (skipped)[/dim]   "
            f"[yellow]{stats['skipped']} dry-run skipped[/yellow]   "
            f"[red]{stats['failed']} failed[/red]"
        )
    if run_id is not None:
        console.print(
            f"[dim]Run {run_id} ({run_mode}): jobs={jobs}, preflight {preflight_seconds:.2f}s, "
            f"download phase {download_phase_seconds:.2f}s, total {total_seconds:.2f}s[/dim]"
        )
    console.print()


def process_urls(
    urls: list[str],
    dry_run: bool,
    config: Any,
    cache: Any,
    playlist_name: str | None = None,
    jobs: int = 3,
    benchmark: bool = False,
    console: Console | None = None,
) -> None:
    """Process URLs through preflight, parallel download, and serialized commit."""
    active_console = console or Console()
    run_started_perf = perf_counter()
    run_mode = "benchmark" if benchmark else "normal"
    run_id = cache.start_run(
        total_urls=len(urls),
        run_mode=run_mode,
        jobs=jobs,
        dry_run=dry_run,
        playlist_name=playlist_name,
    )

    reports: dict[int, TrackReport] = {}
    stats = {
        "downloaded": 0,
        "benchmarked": 0,
        "in_library": 0,
        "failed": 0,
        "cached": 0,
        "skipped": 0,
    }
    preflight_seconds = 0.0
    download_phase_seconds = 0.0
    queued_downloads = 0
    download_tasks: list[DownloadTask] = []

    try:
        if benchmark:
            active_console.print(
                "[dim]Benchmark mode: skipping metadata, URL cache, library checks, "
                "tagging, and Apple Music import; cleaning throwaway staging files.[/dim]"
            )
        if not benchmark and jobs == 1:
            reports, stats = _process_urls_serial(
                urls=urls,
                dry_run=dry_run,
                config=config,
                cache=cache,
                console=active_console,
                playlist_name=playlist_name,
            )
            download_phase_seconds = perf_counter() - run_started_perf
        else:
            download_tasks, reports, stats = _preflight(
                urls=urls,
                dry_run=dry_run,
                benchmark=benchmark,
                jobs=jobs,
                config=config,
                cache=cache,
                console=active_console,
            )
            preflight_seconds = perf_counter() - run_started_perf
            queued_downloads = len(download_tasks)

            if download_tasks:
                download_phase_start = perf_counter()
                _run_parallel_downloads(
                    download_tasks=download_tasks,
                    jobs=jobs,
                    benchmark=benchmark,
                    config=config,
                    cache=cache,
                    console=active_console,
                    playlist_name=playlist_name,
                    reports=reports,
                    stats=stats,
                )
                download_phase_seconds = perf_counter() - download_phase_start

        _render_results(urls=urls, reports=reports, console=active_console)
    finally:
        total_seconds = perf_counter() - run_started_perf
        _persist_run_metrics(
            cache=cache,
            run_id=run_id,
            reports=reports,
            stats=stats,
            preflight_seconds=preflight_seconds,
            download_phase_seconds=download_phase_seconds,
            total_seconds=total_seconds,
            queued_downloads=queued_downloads,
        )
        _cleanup_run_root(download_tasks)

    _print_summary(
        stats=stats,
        console=active_console,
        run_id=run_id,
        run_mode=run_mode,
        jobs=jobs,
        preflight_seconds=preflight_seconds,
        download_phase_seconds=download_phase_seconds,
        total_seconds=total_seconds,
    )
