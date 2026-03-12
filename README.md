# que

An automated music manager. Download playlists and syncronize them across platforms (Apple Music, Spotify, YouTube) with one click.

## Install

```bash
chmod +x install.sh && ./install.sh
```

Requires Python 3.11+ and Homebrew (for yt-dlp). The install script handles everything.

For development:

```bash
uv run pytest
uv run que --help
```

While working in the repo, prefer `uv run que ...` so you execute the current source tree. Rerunning `./install.sh` now refreshes the globally installed `que` tool too.

## Usage

```
que                      # read clipboard → sync to Apple Music
que <URL> [<URL> ...]    # process one or more URLs directly
que --dry-run            # preview what would be downloaded (no downloads)
que --benchmark          # benchmark download-engine throughput only
que --jobs 3             # download eligible tracks with 3 workers
que --no-cache           # re-check every URL even if previously processed
que --threshold 75       # override fuzzy-match threshold for this run
que --playlist "Road Trip"  # add imported tracks to an Apple Music playlist
que list                 # show recent processing history
que runs                 # show recent run-level performance metrics
que list --status downloaded|in_library|failed
que config               # show config + optional helper wizard
que config --no-wizard   # show config without entering the wizard
que config edit          # create/edit config in $EDITOR / $VISUAL
```

### Typical workflow

1. Copy the playlist URL to clipboard
2. In your terminal, run: `que`
3. Tracks already in your library are skipped. New tracks are downloaded and added to Apple Music.

### Benchmarking the download engine

Use benchmark mode when you want repeatable download-engine comparisons on the same URLs.
This is intentionally not end-to-end sync benchmarking.

```bash
que --benchmark --jobs 1 <URL>
que --benchmark --jobs 2 <URL>
que --benchmark --jobs 3 <URL>
que runs
```

Benchmark mode intentionally:

- skips metadata resolution
- skips URL cache reads/writes
- skips library checks
- skips tagging
- skips Apple Music import
- downloads into throwaway staging directories and cleans them up after each track

If you want end-to-end timings instead, run normal `que ...` and compare the resulting entries in `que runs`.

## Config

`que` works out of the box with no config needed. The config file lives at
`~/.config/que/config.toml` by default. You can view it with `que config`,
create it with `que config init`, edit it with `que config edit`, and use the
built-in helper wizard from `que config` in an interactive terminal.

```toml
[library]
paths = [
  "~/Music/Music/Media.localized",
  "~/Music/iTunes/iTunes Media",
]
# Minimum confidence (0–100) to consider a track as "already in library".
# Higher = stricter matching = more downloads. Default: 85.
fuzzy_threshold = 85

[download]
staging_dir = "~/Downloads/que_staging"
format = "m4a"

[import]
use_music_app = true        # notify Music.app via AppleScript
mode = "move_then_music"    # current supported import strategy
destination = "~/Music/Music/Media.localized/Music"

[cache]
path = "~/.local/share/que/cache.db"
```

## Performance metrics

Every run now writes aggregate and per-track timing data into the cache DB:

- `processing_runs`: run mode, jobs, total URLs, queued downloads, run timings, downloaded bytes, average download rate, failure counts
- `processing_run_items`: per-track queue wait, download time, tag/import time, file size, worker name, failure stage

Use `que runs` for a quick terminal view. The `Mode` column distinguishes normal sync runs from benchmark runs. For deeper analysis, inspect `~/.local/share/que/cache.db` directly.

## Architecture

```
que/
├── main.py        CLI entry point, processing loop
├── pipeline.py    sequential preflight + parallel download pipeline
├── config.py      XDG config loader (~/.config/que/config.toml)
├── config_cli.py  `que config` workflow
├── cache.py       SQLite URL cache + run metrics (~/.local/share/que/cache.db)
├── clipboard.py   macOS clipboard reader + URL parser
├── resolver.py    yt-dlp metadata fetching (no download)
├── library.py     LibraryChecker Protocol + FuzzyLibraryChecker
├── downloader.py  yt-dlp audio download to staging dir
├── tagger.py      mutagen ID3/MP4 tagging
└── importer.py    file move + Music.app AppleScript integration
```

### Replacing the library checker with an AI model

`library.py` defines a `LibraryChecker` Protocol. To swap in a semantic/AI checker:

```python
# your_ai_checker.py
from que.library import CheckResult

class AILibraryChecker:
    def is_in_library(self, artist: str, title: str) -> CheckResult:
        # call your model / embedding search here
        ...
```

Then in `main.py`, replace:
```python
checker = FuzzyLibraryChecker(...)
```
with:
```python
checker = AILibraryChecker(...)
```

No other changes needed.

## Roadmap

The phased contributor roadmap now lives in [ROADMAP.md](ROADMAP.md). It covers the planned order for CLI improvements, parallel downloads, shared local service work, quick-access clients, source expansion, and a separate intelligent library search feature.

Phase 2 also ramps worker startup slightly and limits each ExtractAudio ffmpeg postprocessor to one thread to reduce startup CPU spikes on some systems.
Single-worker runs keep the old serial flow so the first eligible track starts downloading immediately instead of waiting for full-playlist preflight.
Downloads now explicitly request audio-only formats from yt-dlp instead of relying on its default format choice.
Browser cookies are no longer sent on the default fast path; que retries with Chrome cookies only if the initial yt-dlp attempt fails.
