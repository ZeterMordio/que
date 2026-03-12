# que

An automated music manager. Download playlists and syncronize them across your music libraries with one click (Apple Music, Spotify, YouTube).

## Install

```bash
chmod +x install.sh && ./install.sh
```

Requires Python 3.11+ and Homebrew (for yt-dlp). The install script handles everything.

For development:

```bash
uv run pytest
```

## Usage

```
que                      # read clipboard → sync to Apple Music
que <URL> [<URL> ...]    # process one or more URLs directly
que --dry-run            # preview what would be downloaded (no downloads)
que --no-cache           # re-check every URL even if previously processed
que --threshold 75       # override fuzzy-match threshold for this run
que --playlist "Road Trip"  # add imported tracks to an Apple Music playlist
que list                 # show recent processing history
que list --status downloaded|in_library|failed
que config               # show config + optional helper wizard
que config --no-wizard   # show config without entering the wizard
que config edit          # create/edit config in $EDITOR / $VISUAL
```

### Typical workflow

1. Copy the playlist URL to clipboard
2. In your terminal, run: `que`
3. Tracks already in your library are skipped. New tracks are downloaded and added to Apple Music.

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

## Architecture

```
que/
├── main.py        CLI entry point, processing loop
├── config.py      XDG config loader (~/.config/que/config.toml)
├── config_cli.py  `que config` workflow
├── cache.py       SQLite URL cache (~/.local/share/que/cache.db)
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
