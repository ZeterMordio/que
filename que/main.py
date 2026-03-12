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

from rich.console import Console
from rich.table import Table

from .cache import Cache, _NullCache
from .clipboard import get_urls_from_clipboard, normalize_url, parse_urls
from .config import load_config
from .config_cli import cmd_config
from .pipeline import process_urls
from .resolver import expand_playlist, is_playlist_url

console = Console()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _status_color(status: str) -> str:
    return {
        "downloaded": "green",
        "in_library": "cyan",
        "failed": "red",
        "skipped": "yellow",
    }.get(status, "white")


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


# ── `que list` subcommand ─────────────────────────────────────────────────────

def cmd_list(cache: Cache, status_filter: str | None) -> None:
    """Print the recent processing history from cache."""
    rows = cache.recent(limit=50, status_filter=status_filter)

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


def cmd_runs(cache: Cache, limit: int) -> None:
    """Print recent aggregate run metrics from cache."""
    rows = cache.recent_runs(limit=limit)

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


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    """Run the que CLI."""
    import argparse

    def positive_int(value: str) -> int:
        parsed = int(value)
        if parsed < 1:
            raise argparse.ArgumentTypeError("--jobs must be >= 1")
        return parsed

    # ── Handle `que list [--status ...]` before normal parsing ───────────────
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
        config = load_config()
        cache: Cache | _NullCache = Cache(config.cache_path)
        try:
            cmd_list(cache, list_args.status)  # type: ignore[arg-type]
        finally:
            cache.close()
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
        config = load_config()
        cache = Cache(config.cache_path)
        try:
            cmd_runs(cache, runs_args.limit)
        finally:
            cache.close()
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

    # ── Load config & cache ──────────────────────────────────────────────────
    config = load_config()

    if args.threshold is not None:
        config.fuzzy_threshold = args.threshold
    if args.playlist and not config.use_music_app:
        # Reason: playlist assignment only works via Music.app scripting.
        config.use_music_app = True
        console.print(
            "[yellow]Playlist import requires Music.app integration; "
            "enabling it for this run.[/yellow]"
        )

    cache = (
        Cache(config.cache_path)
        if args.benchmark or not args.no_cache
        else _NullCache()
    )

    try:
        # ── Collect URLs ─────────────────────────────────────────────────────
        if args.urls:
            urls: list[str] = []
            for u in args.urls:
                found = parse_urls(u)
                urls.extend(found if found else [normalize_url(u)])
        else:
            console.print("[bold]que[/bold] — reading clipboard…")
            urls = get_urls_from_clipboard()

        if not urls:
            console.print(
                "[yellow]No supported URLs found "
                "(clipboard is empty or contains no YouTube / SoundCloud links).[/yellow]"
            )
            sys.exit(0)

        # ── Expand any playlist URLs into individual track URLs ───────────────
        expanded: list[str] = []
        for url in urls:
            if is_playlist_url(url):
                with console.status("  Expanding playlist…", spinner="dots"):
                    tracks = expand_playlist(url)
                console.print(
                    f"  📋 Playlist expanded → [bold]{len(tracks)}[/bold] tracks"
                )
                expanded.extend(tracks)
            else:
                expanded.append(url)
        urls = expanded

        console.print(
            f"Found [bold]{len(urls)}[/bold] URL(s)"
            + (" [dim](dry-run)[/dim]" if args.dry_run else "")
        )

        process_urls(
            urls,
            dry_run=args.dry_run,
            config=config,
            cache=cache,
            playlist_name=args.playlist,
            jobs=args.jobs,
            benchmark=args.benchmark,
            console=console,
        )

    finally:
        cache.close()


if __name__ == "__main__":
    main()
