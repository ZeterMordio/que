# Architecture

High-level implementation notes for `que`.

## Product Shape

Current shipped scope:

- macOS-first CLI
- YouTube track and playlist ingestion
- Apple Music import workflow
- local daemon-backed queue and event stream
- local cache and run metrics

The codebase is intentionally small and centered around one daemon-backed pipeline with a thin CLI client.

## Core Components

- `que/main.py`: CLI entry point, daemon-aware subcommands, and event rendering
- `que/pipeline.py`: main processing pipeline plus structured event emission
- `que/resolver.py`: playlist expansion and metadata resolution
- `que/library.py`: local library indexing and fuzzy matching
- `que/downloader.py`: `yt-dlp` download orchestration
- `que/tagger.py`: audio metadata tagging
- `que/importer.py`: move files into the library folder and notify Apple Music
- `que/cache.py`: URL history plus run-level and per-track metrics
- `que/config.py` and `que/config_cli.py`: config loading, rendering, and terminal workflow
- `que/service/`: daemon request models, queue state, Unix-socket HTTP server, and client helpers

## Pipeline Stages

Normal runs now follow this shape:

1. CLI collects URLs from the clipboard or command line and submits a job to the daemon.
2. The daemon expands playlists into individual track URLs.
3. The daemon preflights each track:
   - metadata lookup
   - cache check
   - local library check
4. Download only the remaining tracks.
5. Tag each finished download.
6. Import each file into the configured library destination and notify Apple Music.
7. Record history and run metrics in SQLite.
8. Stream structured events back to the CLI or any future thin client.

Phase 2 split:

- `--jobs 1`: keep the older serial processing path for fast time-to-first-download
- `--jobs > 1`: keep preflight sequential, parallelize download work only, then serialize tagging/import/cache writes

Why the split exists:

- Apple Music scripting is not a good target for parallelism
- SQLite writes stay simpler and safer on one thread
- per-job staging directories avoid download-path collisions
- final result output stays stable in input order

## Local Service Layer

Phase 3 adds a local Unix-domain-socket daemon as the single owner of queue state.

- Transport: HTTP + JSON over `~/.local/state/que/daemon.sock`
- Queue model: one active job at a time, FIFO for later jobs
- Event model: per-job SSE stream for log lines, item reports, final results, and summary data
- Persistence model: active queue state is in-memory only; completed history stays in SQLite

Current public daemon-backed surfaces:

- `que <URL>`: submit a job and tail its event stream
- `que list`: read recent processed URLs through the daemon
- `que runs`: read recent run metrics through the daemon
- `que status`: inspect active and queued jobs
- `que daemon --foreground` / `que daemon stop`: debug and lifecycle helpers

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

`que/library.py` exposes a `LibraryChecker`-style interface through the current fuzzy checker behavior. A future semantic or AI-backed search/matching implementation should plug in at that layer rather than rewriting the rest of the daemon-backed pipeline.

Other current boundaries:

- downloader vs importer: download concerns stay separate from Apple Music concerns
- config loading vs config CLI: runtime config stays independent from the terminal wizard
- cache metrics vs user output: reporting can evolve without changing the stored history model
- pipeline vs renderer: the pipeline emits events; the CLI renders them

## Related Docs

- [DEVELOPMENT.md](DEVELOPMENT.md)
- [CHANGELOG.md](CHANGELOG.md)
