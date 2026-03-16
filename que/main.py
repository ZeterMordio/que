"""
main.py — CLI entry point for `que`.

Usage
-----
  que                      Read clipboard, sync playlist to Apple Music
  que <URL> [<URL> ...]    Process one or more URLs directly
  que --dry-run            Show what would be downloaded, without downloading
  que --benchmark          Benchmark download throughput with throwaway staging
  que --jobs 3             Download eligible tracks with 3 workers
  que --no-cache           Ignore the URL cache for this run
  que --playlist NAME      Add imported tracks to a named Apple Music playlist
  que list                 Show recent processing history
  que runs                 Show recent run metrics
  que status               Show daemon queue state
  que daemon --foreground  Run the local daemon in the foreground
  que daemon stop          Stop an idle daemon
  que config               Show the active config
  que list --status downloaded|in_library|failed

Expansion roadmap (planned)
---------------------------
  que room                 Grab the current QueUp room queue
  que config               Print / edit the active config
  AILibraryChecker         Drop-in replacement for FuzzyLibraryChecker
                           (see library.py for the Protocol interface)
"""
from __future__ import annotations

import sys
import time

from rich.console import Console
from rich.table import Table

from .clipboard import get_urls_from_clipboard, normalize_url, parse_urls
from .config_cli import cmd_config
from .service.client import DaemonClient
from .service.models import JobRequest, JobSnapshot, JobSubmission, ServiceEvent
from .service.server import serve_forever

console = Console()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _status_color(status: str) -> str:
    return {
        "downloaded": "green",
        "benchmarked": "green",
        "in_library": "cyan",
        "failed": "red",
        "skipped": "yellow",
        "cached": "magenta",
    }.get(status, "white")


def _job_state_color(state: str) -> str:
    return {
        "queued": "yellow",
        "running": "cyan",
        "completed": "green",
        "failed": "red",
    }.get(state, "white")


def _format_bytes(value: int) -> str:
    """Render bytes using a compact binary unit."""
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    size = float(value)
    unit = units[0]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            break
        size /= 1024
    decimals = 0 if unit == "B" else 1
    return f"{size:.{decimals}f} {unit}"


def _format_rate(value: float | None) -> str:
    """Render a bytes/sec rate for recent run output."""
    if value is None:
        return "—"
    return f"{_format_bytes(int(value))}/s"


def _format_eta_seconds(value: int | None) -> str:
    """Render an ETA seconds value for coarse queue-progress output."""
    if value in (None, 0):
        return "—"
    minutes, seconds = divmod(int(value), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:d}:{seconds:02d}"


# ── `que list` subcommand ─────────────────────────────────────────────────────

def cmd_list(cache_like, status_filter: str | None) -> None:
    """Print the recent processing history from cache."""
    rows = cache_like.recent(limit=50, status_filter=status_filter)

    if not rows:
        console.print("[dim]No history found.[/dim]")
        return

    table = Table(title="que history", show_lines=False, highlight=True)
    table.add_column("Status", style="bold", width=12)
    table.add_column("Artist")
    table.add_column("Title")
    table.add_column("Date", style="dim", width=12)

    for _url, artist, title, status, ts in rows:
        color = _status_color(status)
        table.add_row(
            f"[{color}]{status}[/{color}]",
            artist or "[dim]—[/dim]",
            title or "[dim]—[/dim]",
            ts[:10],
        )

    console.print(table)


def cmd_runs(cache_like, limit: int) -> None:
    """Print recent aggregate run metrics from cache."""
    rows = cache_like.recent_runs(limit=limit)

    if not rows:
        console.print("[dim]No run metrics found.[/dim]")
        return

    table = Table(title="que runs", show_lines=False, highlight=True)
    table.add_column("Run", style="bold")
    table.add_column("Mode", no_wrap=True)
    table.add_column("Date", style="dim", no_wrap=True)
    table.add_column("Jobs", justify="right")
    table.add_column("URLs", justify="right")
    table.add_column("Downloaded", justify="right", no_wrap=True)
    table.add_column("Failed", justify="right", no_wrap=True)
    table.add_column("Time", justify="right")
    table.add_column("Avg rate", justify="right", no_wrap=True)

    for row in rows:
        failed = str(row.failed_count)
        if row.download_failed_count or row.import_failed_count:
            failed = (
                f"{row.failed_count} "
                f"[dim](d:{row.download_failed_count}/i:{row.import_failed_count})[/dim]"
            )
        downloaded = str(row.downloaded_count)
        if row.downloaded_bytes:
            downloaded = (
                f"{row.downloaded_count} "
                f"[dim]({_format_bytes(row.downloaded_bytes)})[/dim]"
            )
        total = "—" if row.total_seconds is None else f"{row.total_seconds:.2f}s"
        table.add_row(
            str(row.run_id),
            row.run_mode,
            row.started_at.strftime("%Y-%m-%d %H:%M"),
            str(row.jobs),
            str(row.total_urls),
            downloaded,
            failed,
            total,
            _format_rate(row.average_download_bytes_per_second),
        )

    console.print(table)


def cmd_status(client: DaemonClient) -> None:
    """Print active and queued daemon jobs."""
    queue = client.get_queue()
    active = queue["active"]
    queued = queue["queued"]

    if active is None and not queued:
        console.print("[dim]No queued or running jobs.[/dim]")
        return

    table = Table(title="que status", show_lines=False, highlight=True)
    table.add_column("Job", style="bold")
    table.add_column("State")
    table.add_column("Pos", justify="right")
    table.add_column("Done", justify="right")
    table.add_column("Failed", justify="right")
    table.add_column("URLs", justify="right")
    table.add_column("Mode", no_wrap=True)

    snapshots = ([active] if active is not None else []) + queued
    for snapshot in snapshots:
        color = _job_state_color(snapshot.state)
        table.add_row(
            snapshot.job_id,
            f"[{color}]{snapshot.state}[/{color}]",
            "—" if snapshot.position == 0 else str(snapshot.position),
            str(snapshot.processed_count),
            str(snapshot.failed_count),
            str(snapshot.total_urls),
            snapshot.run_mode,
        )

    console.print(table)


def _render_results_event(reports: list[dict[str, object]]) -> None:
    """Render the final ordered results table from an SSE payload."""
    console.print()
    table = Table(title="que results", show_lines=False, highlight=True)
    table.add_column("#", style="dim", width=4)
    table.add_column("Status", style="bold", width=12)
    table.add_column("Track")
    table.add_column("Notes")

    for index, report in enumerate(reports, 1):
        status = str(report["status"])
        color = _status_color(status)
        artist = str(report["artist"] or "")
        title = str(report["title"] or "")
        note = str(report["note"])
        track = f"{artist} - {title}" if artist else (title or "—")
        table.add_row(
            str(index),
            f"[{color}]{status}[/{color}]",
            track,
            note,
        )

    console.print(table)


def _render_summary_event(payload: dict[str, object]) -> None:
    """Render the final run summary from an SSE payload."""
    stats = payload["stats"]
    run_mode = str(payload["run_mode"])
    run_id = payload["run_id"]
    jobs = int(payload["jobs"])
    preflight_seconds = float(payload["preflight_seconds"])
    download_phase_seconds = float(payload["download_phase_seconds"])
    total_seconds = float(payload["total_seconds"])

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


def _render_service_event(event: ServiceEvent) -> None:
    """Render one service event into the current CLI session."""
    if event.type == "results":
        reports = event.payload.get("reports", [])
        if isinstance(reports, list):
            _render_results_event(reports)
        return

    if event.type == "summary":
        _render_summary_event(event.payload)
        return

    if event.type in {"summary_counts", "summary_run", "item_report"}:
        return

    if event.message:
        console.print(event.message)


def _collect_urls(raw_urls: list[str]) -> list[str]:
    """Normalize direct CLI URL input."""
    urls: list[str] = []
    for raw in raw_urls:
        found = parse_urls(raw)
        urls.extend(found if found else [normalize_url(raw)])
    return urls


def _stream_job(
    client: DaemonClient,
    job_id: str,
    *,
    suppress_lifecycle: bool = False,
) -> None:
    """Tail a daemon-backed job until the server closes the event stream."""
    lifecycle_types = {"job_queued", "queue_position_updated", "job_started"}
    for event in client.stream_events(job_id):
        if suppress_lifecycle and event.type in lifecycle_types:
            continue
        _render_service_event(event)


def _wait_for_job_turn(client: DaemonClient, submission: JobSubmission) -> JobSnapshot:
    """Keep the CLI attached while a queued job waits behind another job.

    Args:
        client: Daemon client.
        submission: Initial submission result.

    Returns:
        Fresh snapshot once the job starts or finishes while queued.
    """
    snapshot = submission.snapshot
    if snapshot.state != "queued":
        return snapshot

    printed_signature: tuple[object, ...] | None = None
    while True:
        queue = client.get_queue()
        own = client.get_job(snapshot.job_id)
        if own.state != "queued":
            return own

        active = queue["active"]
        signature = (
            own.position,
            active.job_id if active else None,
            active.phase if active else None,
            active.detail if active else None,
            active.processed_count if active else None,
            active.total_urls if active else None,
            round(active.progress_fraction, 3) if active else None,
            active.eta_seconds if active else None,
        )
        if signature != printed_signature:
            printed_signature = signature
            if active is None:
                console.print(
                    "[dim]Queued job "
                    f"{own.job_id} at position {own.position}; "
                    "waiting for daemon worker.[/dim]"
                )
            else:
                progress = int((active.progress_fraction or 0.0) * 100)
                eta = _format_eta_seconds(active.eta_seconds)
                console.print(
                    f"[dim]Queued job {own.job_id} at position {own.position}. "
                    f"Active {active.job_id}: "
                    f"{active.phase.replace('_', ' ')} — "
                    f"{active.detail or active.last_message or 'Working'} "
                    f"({active.processed_count}/{active.total_urls}, {progress}%"
                    + (f", ETA {eta}" if eta != "—" else "")
                    + ").[/dim]"
                )
        time.sleep(0.5)


def _print_error_and_exit(message: str) -> None:
    """Render a user-facing error and exit non-zero."""
    console.print(f"[red]{message}[/red]")
    raise SystemExit(1)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    """Run the que CLI."""
    import argparse

    def positive_int(value: str) -> int:
        parsed = int(value)
        if parsed < 1:
            raise argparse.ArgumentTypeError("--jobs must be >= 1")
        return parsed

    # ── Handle daemon-backed subcommands before normal parsing ────────────────
    # This avoids argparse confusing URLs with subcommand names when both
    # positional args and subparsers are present.
    if len(sys.argv) > 1 and sys.argv[1] == "list":
        list_parser = argparse.ArgumentParser(prog="que list")
        list_parser.add_argument(
            "--status",
            choices=["downloaded", "in_library", "failed", "skipped"],
            default=None,
            help="Filter history by status.",
        )
        list_args = list_parser.parse_args(sys.argv[2:])
        try:
            cmd_list(DaemonClient(), list_args.status)
        except RuntimeError as exc:
            _print_error_and_exit(str(exc))
        return

    if len(sys.argv) > 1 and sys.argv[1] == "config":
        raise SystemExit(cmd_config(sys.argv[2:]))

    if len(sys.argv) > 1 and sys.argv[1] == "runs":
        runs_parser = argparse.ArgumentParser(prog="que runs")
        runs_parser.add_argument(
            "--limit",
            type=int,
            default=20,
            metavar="N",
            help="How many recent runs to show. (default: 20)",
        )
        runs_args = runs_parser.parse_args(sys.argv[2:])
        try:
            cmd_runs(DaemonClient(), runs_args.limit)
        except RuntimeError as exc:
            _print_error_and_exit(str(exc))
        return

    if len(sys.argv) > 1 and sys.argv[1] == "status":
        try:
            cmd_status(DaemonClient())
        except RuntimeError as exc:
            _print_error_and_exit(str(exc))
        return

    if len(sys.argv) > 1 and sys.argv[1] == "daemon":
        daemon_parser = argparse.ArgumentParser(prog="que daemon")
        daemon_parser.add_argument(
            "--foreground",
            action="store_true",
            help="Run the daemon in the foreground.",
        )
        daemon_subparsers = daemon_parser.add_subparsers(dest="command")
        daemon_subparsers.add_parser("stop", help="Stop an idle daemon.")
        daemon_args = daemon_parser.parse_args(sys.argv[2:])

        if daemon_args.command == "stop":
            try:
                DaemonClient(autostart=False).stop()
            except RuntimeError as exc:
                _print_error_and_exit(str(exc))
            return

        if daemon_args.foreground:
            try:
                serve_forever()
            except RuntimeError as exc:
                _print_error_and_exit(str(exc))
            return

        daemon_parser.print_help()
        return

    # ── Normal sync / download flow ───────────────────────────────────────────
    parser = argparse.ArgumentParser(
        prog="que",
        description=(
            "Sync and download music from QueUp playlists to Apple Music.\n\n"
            "  que                  Read clipboard and sync\n"
            "  que <URL> [<URL>...] Process URLs directly\n"
            "  que --benchmark      Benchmark download-engine throughput\n"
            "  que list             Show processing history\n"
            "  que runs             Show run metrics\n"
            "  que status           Show daemon queue status\n"
            "  que daemon --foreground\n"
            "                       Run the local daemon in the foreground\n"
            "  que config           View or edit config\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "urls",
        nargs="*",
        metavar="URL",
        help="One or more media URLs (YouTube, SoundCloud). "
             "If omitted, reads from clipboard.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be downloaded without actually downloading.",
    )
    parser.add_argument(
        "--benchmark",
        action="store_true",
        help=(
            "Benchmark the download engine only: skip metadata, cache/library "
            "checks, tagging, and Apple Music import; clean up throwaway "
            "staging files after each track."
        ),
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Ignore the URL cache for this run (re-checks every URL).",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=None,
        metavar="0-100",
        help="Override the fuzzy-match threshold for this run. "
             "Lower = more downloads. (default: 85)",
    )
    parser.add_argument(
        "--playlist",
        default=None,
        metavar="NAME",
        help="Add imported tracks to the named Apple Music playlist.",
    )
    parser.add_argument(
        "--jobs",
        type=positive_int,
        default=3,
        metavar="N",
        help="Number of parallel download workers to use. (default: 3)",
    )

    args = parser.parse_args()
    if args.benchmark and args.dry_run:
        parser.error("--benchmark cannot be combined with --dry-run")
    if args.benchmark and args.playlist:
        parser.error("--benchmark cannot be combined with --playlist")

    # ── Collect URLs and submit to daemon ────────────────────────────────────
    if args.urls:
        console.print("[bold]que[/bold] — using URL arguments…")
        urls = _collect_urls(args.urls)
    else:
        console.print("[bold]que[/bold] — reading clipboard…")
        urls = get_urls_from_clipboard()

    if not urls:
        console.print(
            "[yellow]No supported URLs found "
            "(clipboard is empty or contains no YouTube / SoundCloud links).[/yellow]"
        )
        sys.exit(0)

    request = JobRequest(
        urls=urls,
        dry_run=args.dry_run,
        benchmark=args.benchmark,
        playlist_name=args.playlist,
        jobs=args.jobs,
        threshold_override=args.threshold,
        use_cache=not args.no_cache,
    )
    try:
        client = DaemonClient()
        submission = client.submit_job(request)
        snapshot = submission.snapshot
        if submission.deduplicated:
            if snapshot.state == "running":
                console.print(
                    f"[dim]Attached to existing running job {snapshot.job_id}.[/dim]"
                )
            else:
                console.print(
                    f"[dim]Attached to existing queued job {snapshot.job_id} "
                    f"(position {snapshot.position}).[/dim]"
                )
        waited_snapshot = _wait_for_job_turn(client, submission)
        _stream_job(
            client,
            waited_snapshot.job_id,
            suppress_lifecycle=waited_snapshot.state != submission.snapshot.state,
        )
    except RuntimeError as exc:
        _print_error_and_exit(str(exc))


if __name__ == "__main__":
    main()
