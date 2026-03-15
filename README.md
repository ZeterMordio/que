# que

`que` is a macOS CLI for bringing YouTube tracks and playlists into Apple Music without redownloading songs you already have.

Copy a URL, run one command, and let `que` check your library, download only the missing tracks, tag them, and hand them off to Apple Music.

## What It Does

- Accepts a YouTube track or playlist URL from the clipboard or the command line
- Auto-starts a local daemon so queue state, history, and live progress have one source of truth
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
que                              # From clipboard
que <URL>                        # Direct URL
que --playlist "Blues" <URL>     # Add imported tracks to a playlist directly
que --dry-run <URL>              # Preview download first
que config                       # Start config wizard for help 🧙
```

## Power Usage

### Command Reference

- `que --jobs N <URL>`: use `N` download workers for eligible tracks
- `que --no-cache <URL>`: ignore the URL cache for that run
- `que --threshold 75 <URL>`: override fuzzy matching parameter for that run
- `que list [--status downloaded|in_library|failed|skipped]`: show recent URL history
- `que runs [--limit N]`: show recent run summaries and diagnostics
- `que status`: show active and queued daemon jobs
- `que daemon --foreground`: run the local daemon in the foreground for debugging
- `que daemon stop`: stop an idle daemon cleanly
- `que config init|edit|path`: initialize, edit, or print the config path

### Config Overview

The config file lives at `~/.config/que/config.toml`.

**Use `que config` if you want a guided terminal flow instead of editing TOML manually!**


Most people only need to care about these options:

```toml
[library]
paths = [
  "~/Music/Music/Media.localized",
  "~/Music/iTunes/iTunes Media",
]

[download]
staging_dir = "~/Downloads/que_staging"

[import]
use_music_app = true
destination = "~/Music/Music/Media.localized/Music"
```

What they do:

- `library.paths`: folders that `que` scans when checking whether a track already exists
- `download.staging_dir`: temporary download area before import
- `import.use_music_app`: whether `que` should notify Apple Music after moving files
- `import.destination`: where imported files are moved on disk

### How It Works

`que` now runs through a local daemon-backed pipeline:

1. Read a URL from the clipboard or command line, then submit the job to the local daemon.
2. The daemon expands playlists into individual tracks and owns queue/job state.
3. The daemon resolves metadata, consults the cache, and checks your local library.
4. It downloads only the tracks that still need work.
5. It tags the downloaded files, moves them into your library folder, and notifies Apple Music.
6. It records history and run metrics so later runs and future clients can reuse the same state.

### Diagnostics

- `que list` shows recent per-URL history
- `que runs` shows recent run-level diagnostics
- `que status` shows active/queued jobs in the local daemon
- Config path: `~/.config/que/config.toml`
- Cache DB: `~/.local/share/que/cache.db`
- Daemon socket: `~/.local/state/que/daemon.sock`
- Daemon PID lock: `~/.local/state/que/daemon.pid`

## Future Enhancements

- **Chrome extension:** lightweight companion surface for one-click downloads.
- Add Spotify & SoundCloud support.

## Developer Docs

- [DEVELOPMENT.md](DEVELOPMENT.md)
- [ARCHITECTURE.md](ARCHITECTURE.md)
- [CHANGELOG.md](CHANGELOG.md)
