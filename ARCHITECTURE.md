# Architecture

High-level implementation notes for `que`.

## Product Shape

Current shipped scope:

- macOS-first CLI
- YouTube track and playlist ingestion
- Apple Music import workflow
- local cache and run metrics

The codebase is intentionally small and centered around one CLI pipeline.

## Core Components

- `que/main.py`: CLI entry point, subcommands, config loading, cache wiring
- `que/pipeline.py`: main processing pipeline and ordered result reporting
- `que/resolver.py`: playlist expansion and metadata resolution
- `que/library.py`: local library indexing and fuzzy matching
- `que/downloader.py`: `yt-dlp` download orchestration
- `que/tagger.py`: audio metadata tagging
- `que/importer.py`: move files into the library folder and notify Apple Music
- `que/cache.py`: URL history plus run-level and per-track metrics
- `que/config.py` and `que/config_cli.py`: config loading, rendering, and terminal workflow

## Pipeline Stages

Normal runs follow this shape:

1. Collect URLs from the clipboard or command line.
2. Expand playlists into individual track URLs.
3. Preflight each track:
   - metadata lookup
   - cache check
   - local library check
4. Download only the remaining tracks.
5. Tag each finished download.
6. Import each file into the configured library destination and notify Apple Music.
7. Record history and run metrics.

Phase 2 split:

- `--jobs 1`: keep the older serial processing path for fast time-to-first-download
- `--jobs > 1`: keep preflight sequential, parallelize download work only, then serialize tagging/import/cache writes

Why the split exists:

- Apple Music scripting is not a good target for parallelism
- SQLite writes stay simpler and safer on one thread
- per-job staging directories avoid download-path collisions
- final result output stays stable in input order

## Import Behavior

The current import mode is fixed to `move_then_music`.

That means:

1. Move the downloaded file into the destination folder first.
2. Then ask Apple Music to add it via AppleScript.

Properties of this approach:

- safer failure mode: the file is already in the library folder even if Apple Music scripting fails
- deterministic on-disk layout under `<destination>/<Artist>/Unknown Album/`
- optional playlist placement happens through the AppleScript step

Apple Music integration is best-effort. A script failure does not lose the downloaded file.

## Extension Points

The most important current extension seam is the library checker abstraction.

`que/library.py` exposes a `LibraryChecker`-style interface through the current fuzzy checker behavior. A future semantic or AI-backed search/matching implementation should plug in at that layer rather than rewriting the rest of the pipeline.

Other current boundaries:

- downloader vs importer: download concerns stay separate from Apple Music concerns
- config loading vs config CLI: runtime config stays independent from the terminal wizard
- cache metrics vs user output: reporting can evolve without changing the stored history model

## Related Docs

- [DEVELOPMENT.md](DEVELOPMENT.md)
- [CHANGELOG.md](CHANGELOG.md)
