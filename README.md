# que

`que` is a macOS CLI for bringing YouTube tracks and playlists into Apple Music without redownloading songs you already have.

Copy a URL, run one command, and let `que` check your library, download only the missing tracks, tag them, and hand them off to Apple Music.

## What It Does

- Accepts a YouTube track or playlist URL from the clipboard or the command line
- Checks your local library before downloading, so obvious duplicates are skipped
- Downloads missing tracks, tags them, and imports them into Apple Music
- Can add newly imported tracks to a named Apple Music playlist

## Requirements

- macOS
- Apple Music
- Python 3.11+
- Homebrew recommended for the install flow

Optional but useful:

- Google Chrome, for occasional `yt-dlp` cookie fallback on harder-to-download videos

## Install

1. Clone this repository.
2. Run the installer:

```bash
chmod +x install.sh
./install.sh
```

3. If your music library lives somewhere unusual, run the config helper once:

```bash
que config
```

That is usually enough. The install script handles `uv` and `yt-dlp` for you.

## Quickstart

1. Copy a YouTube track or playlist URL.
2. Run:

```bash
que
```

3. `que` reads the clipboard, checks your library, downloads missing tracks, and imports them into Apple Music.

If you prefer not to use the clipboard, pass the URL directly:

```bash
que "https://youtube.com/playlist?list=..."
```

## Everyday Usage

```bash
que
que "https://youtube.com/watch?v=..."
que "https://youtube.com/playlist?list=..."
que --playlist "Road Trip" "https://youtube.com/playlist?list=..."
que --dry-run "https://youtube.com/playlist?list=..."
que config
```

## Power Usage

### Command Reference

- `que`: read the clipboard and process the current URL
- `que <URL> [<URL> ...]`: process one or more explicit URLs
- `que --dry-run <URL>`: preview what would be downloaded
- `que --playlist "Name" <URL>`: add imported tracks to a named Apple Music playlist
- `que --jobs N <URL>`: use `N` download workers for eligible tracks
- `que --no-cache <URL>`: ignore the URL cache for that run
- `que --threshold 75 <URL>`: override fuzzy matching for that run
- `que list [--status downloaded|in_library|failed|skipped]`: show recent URL history
- `que runs [--limit N]`: show recent run summaries and diagnostics
- `que config`: show the current config and launch the helper wizard in an interactive terminal
- `que config init|edit|path`: initialize, edit, or print the config path

### Config Overview

The config file lives at `~/.config/que/config.toml`.

Most people only need to care about these options:

```toml
[library]
paths = [
  "~/Music/Music/Media.localized",
  "~/Music/iTunes/iTunes Media",
]
fuzzy_threshold = 85

[download]
staging_dir = "~/Downloads/que_staging"

[import]
use_music_app = true
destination = "~/Music/Music/Media.localized/Music"
```

What they do:

- `library.paths`: folders that `que` scans when checking whether a track already exists
- `library.fuzzy_threshold`: higher means stricter matching, lower means more downloads
- `download.staging_dir`: temporary download area before import
- `import.use_music_app`: whether `que` should notify Apple Music after moving files
- `import.destination`: where imported files are moved on disk

Use `que config` if you want a guided terminal flow instead of editing TOML manually.

### How It Works

`que` follows a simple pipeline:

1. Read a URL from the clipboard or command line, then expand playlists into individual tracks.
2. Resolve metadata, consult the cache, and check your local library.
3. Download only the tracks that still need work.
4. Tag the downloaded files, move them into your library folder, and notify Apple Music.
5. Record history so later runs can skip known results quickly.

### Diagnostics

- `que list` shows recent per-URL history
- `que runs` shows recent run-level diagnostics
- Config path: `~/.config/que/config.toml`
- Cache DB: `~/.local/share/que/cache.db`

## Current Focus

Current focus: keep the Apple Music workflow fast and reliable, keep improving large-playlist throughput and diagnostics, and later expand outward with lightweight companion surfaces, more source support, and a smarter local library-search experience.

## Developer Docs

- [DEVELOPMENT.md](DEVELOPMENT.md)
- [ARCHITECTURE.md](ARCHITECTURE.md)
- [CHANGELOG.md](CHANGELOG.md)
