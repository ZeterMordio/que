# que

CLI tool for syncing and downloading YouTube music playlists to Apple Music.

## Install

```bash
chmod +x install.sh && ./install.sh
```

Requires Python 3.11+ and Homebrew (for yt-dlp). The install script handles everything.

## Usage

```
que                      # read clipboard → sync to Apple Music
que <URL> [<URL> ...]    # process one or more URLs directly
que --dry-run            # preview what would be downloaded (no downloads)
que --no-cache           # re-check every URL even if previously processed
que --threshold 75       # override fuzzy-match threshold for this run
que list                 # show recent processing history
que list --status downloaded|in_library|failed
```

### Typical workflow

1. Copy the playlist URL to clipboard
2. In your terminal, run: `que`
3. Tracks already in your library are skipped. New tracks are downloaded and added to Apple Music.

## Config

`que` works out of the box with no config needed. To customise, create `~/.querc`:

```toml
[library]
path = "~/Music/iTunes/iTunes Media/Apple Music"
# Minimum confidence (0–100) to consider a track as "already in library".
# Higher = stricter matching = more downloads. Default: 85.
fuzzy_threshold = 85

[download]
staging_dir = "~/Downloads/que_staging"
format = "m4a"

[import]
use_music_app = true        # notify Music.app via AppleScript
fallback_to_folder = true   # always move file to library folder first

[cache]
path = "~/.que/cache.db"
```

## Architecture

```
que/
├── main.py        CLI entry point, processing loop
├── config.py      ~/.querc loader
├── cache.py       SQLite URL cache (~/.que/cache.db)
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

- [ ] `que room` — pull the current QueUp room queue directly
- [ ] `que config` — view/edit config from the terminal
- [ ] Parallel downloads
- [ ] Spotify support via `spotdl`
- [ ] AI-powered library matching (semantic embeddings)
- [ ] `--playlist` flag to add downloaded tracks to a named Apple Music playlist
