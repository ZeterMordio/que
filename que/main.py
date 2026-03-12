"""
main.py — CLI entry point for `que`.

Usage
-----
  que                      Read clipboard, sync playlist to Apple Music
  que <URL> [<URL> ...]    Process one or more URLs directly
  que --dry-run            Show what would be downloaded, without downloading
  que --no-cache           Ignore the URL cache for this run
  que --playlist NAME      Add imported tracks to a named Apple Music playlist
  que list                 Show recent processing history
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
from .downloader import download_track
from .importer import import_to_apple_music
from .library import FuzzyLibraryChecker
from .resolver import expand_playlist, is_playlist_url, resolve_metadata
from .tagger import tag_file

console = Console()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _status_color(status: str) -> str:
    return {
        "downloaded": "green",
        "in_library": "cyan",
        "failed": "red",
        "skipped": "yellow",
    }.get(status, "white")


def _label(artist: str, title: str) -> str:
    if artist:
        return f"[bold]{artist}[/bold] – {title}"
    return f"[bold]{title}[/bold]"


# ── Core processing loop ──────────────────────────────────────────────────────

def process_urls(
    urls: list[str],
    dry_run: bool,
    config,
    cache,
    playlist_name: str | None = None,
) -> None:
    """Process URLs from metadata resolution through import."""
    checker = FuzzyLibraryChecker(
        library_paths=config.library_paths,
        threshold=config.fuzzy_threshold,
    )

    # ── Warn early if library is unreachable or empty ────────────────────────
    existing = [p for p in config.library_paths if p.exists()]
    if not existing:
        paths_str = "\n     ".join(str(p) for p in config.library_paths)
        console.print(
            f"[yellow]⚠  No library paths found. Checked:[/yellow]\n     {paths_str}\n"
            f"   Add your music folder to [bold]~/.config/que/config.toml[/bold]:\n"
            f"   [dim][[library]]\n   paths = [\"~/Music/your-folder\"][/dim]"
        )
    elif not checker._tracks:
        paths_str = ", ".join(str(p) for p in existing)
        console.print(
            "[yellow]⚠  Library paths exist but no audio files found:[/yellow] "
            f"[dim]{paths_str}[/dim]"
        )
    else:
        source_label = f"via {checker._source}"
        if checker._source == "filesystem":
            source_label += f" ({', '.join(str(p) for p in existing)})"
        console.print(
            f"[dim]📚 Library: {len(checker._tracks)} tracks {source_label}[/dim]"
        )

    stats = {"downloaded": 0, "in_library": 0, "failed": 0, "cached": 0, "skipped": 0}

    for i, url in enumerate(urls, 1):
        console.print(f"\n[dim]({i}/{len(urls)})[/dim] [dim]{url}[/dim]")

        # ── Cache hit ────────────────────────────────────────────────────────
        cached = cache.get(url)
        if cached:
            console.print(
                f"  ⏭  [dim]Cached ({cached.status}):[/dim] "
                f"{_label(cached.artist, cached.title)}"
            )
            stats["cached"] += 1
            continue

        # ── Resolve metadata ─────────────────────────────────────────────────
        with console.status("  Fetching metadata…", spinner="dots"):
            meta = resolve_metadata(url)

        if not meta:
            console.print("  [red]✗  Could not fetch metadata — skipping[/red]")
            cache.set(url, "", "", "failed")
            stats["failed"] += 1
            continue

        console.print(f"  🎵 {_label(meta.artist, meta.title)}")
        if meta.raw_title != meta.title:
            console.print(f"     [dim]raw: {meta.raw_title}[/dim]")

        # ── Library check ────────────────────────────────────────────────────
        result = checker.is_in_library(meta.artist, meta.title)

        if result.in_library:
            console.print(
                f"  [cyan]✓  Already in library[/cyan]  "
                f"[dim]{result.reason}[/dim]"
            )
            cache.set(url, meta.title, meta.artist, "in_library")
            stats["in_library"] += 1
            continue

        console.print(
            f"  [yellow]↓  Not in library[/yellow]  "
            f"[dim]{result.reason}[/dim]"
        )

        if dry_run:
            console.print("  [dim][dry-run] Would download[/dim]")
            stats["skipped"] += 1
            continue

        # ── Download ─────────────────────────────────────────────────────────
        with console.status("  Downloading…", spinner="dots"):
            downloaded = download_track(url, config.staging_dir, config.audio_format)

        if not downloaded:
            console.print("  [red]✗  Download failed[/red]")
            cache.set(url, meta.title, meta.artist, "failed")
            stats["failed"] += 1
            continue

        console.print(f"  [green]✓  Downloaded:[/green] {downloaded.name}")

        # ── Tag ──────────────────────────────────────────────────────────────
        tagged = tag_file(downloaded, meta.artist, meta.title)
        if tagged:
            console.print("  🏷  Tagged with artist / title metadata")

        # ── Import to Apple Music ────────────────────────────────────────────
        ok, msg = import_to_apple_music(
            downloaded,
            meta.artist,
            config.import_destination,
            config.use_music_app,
            playlist_name=playlist_name,
        )

        if ok:
            console.print(f"  [green]✓  {msg}[/green]")
            cache.set(url, meta.title, meta.artist, "downloaded")
            stats["downloaded"] += 1
        else:
            console.print(f"  [red]✗  {msg}[/red]")
            cache.set(url, meta.title, meta.artist, "failed")
            stats["failed"] += 1

    # ── Summary ──────────────────────────────────────────────────────────────
    console.print()
    console.rule("[bold]Summary[/bold]")
    console.print(
        f"  [green]{stats['downloaded']} downloaded & imported[/green]   "
        f"[cyan]{stats['in_library']} already in library[/cyan]   "
        f"[dim]{stats['cached']} cached (skipped)[/dim]   "
        f"[yellow]{stats['skipped']} dry-run skipped[/yellow]   "
        f"[red]{stats['failed']} failed[/red]"
    )
    console.print()


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


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    """Run the que CLI."""
    import argparse

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

    # ── Normal sync / download flow ───────────────────────────────────────────
    parser = argparse.ArgumentParser(
        prog="que",
        description=(
            "Sync and download music from QueUp playlists to Apple Music.\n\n"
            "  que                  Read clipboard and sync\n"
            "  que <URL> [<URL>...] Process URLs directly\n"
            "  que list             Show processing history\n"
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

    args = parser.parse_args()

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

    cache = _NullCache() if args.no_cache else Cache(config.cache_path)  # type: ignore[assignment]

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
        )

    finally:
        cache.close()


if __name__ == "__main__":
    main()
