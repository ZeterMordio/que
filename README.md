# que

`que` is a macOS CLI and menu bar app for bringing YouTube tracks and playlists into Apple Music without redownloading songs you already have.

Copy a URL, run one command, and let `que` check your library, download only the missing tracks, tag them, and hand them off to Apple Music.

## What It Does

- Accepts a YouTube track or playlist URL from the clipboard or the command line
- Auto-starts a local daemon so queue state, history, and live progress have one source of truth
- Ships a lightweight menu bar client for quick status, one-click sync, and graphical settings
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
- Google Chrome, for the menu bar app's active-tab fallback on right click

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

That is usually enough. The install script handles `uv`, `yt-dlp`, and `Que Menu.app` for you.

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

Important YouTube behavior:

- `watch?v=...` and `youtu.be/...` URLs are treated as single tracks, even when YouTube adds `list=` / `index=` mix context
- use an explicit `/playlist?list=...` URL when you want the whole playlist expanded

## Everyday Usage

```bash
que                              # From clipboard
que <URL>                        # Direct URL
que --playlist "Blues" <URL>     # Add imported tracks to a playlist directly
que --dry-run <URL>              # Preview download first
que config                       # Start config wizard for help 🧙
que-menu                         # Launch the menu bar app from a terminal
```

## Menu Bar App

`Que Menu.app` is a thin client on top of the same local daemon used by the CLI.

- Left click opens the dropdown for status, recent tracks, quick sync, and `Settings…`
- Right click immediately runs the default intake flow without opening the menu
- If the clipboard has no actionable URL and Google Chrome is frontmost, right click falls back to the active tab URL
- While a job is active, the menu-bar title itself now reflects the live phase, and the dropdown puts the current track first, followed by phase, progress, queue, and ETA
- The live rows keep refreshing while the dropdown stays open, so you do not need to close and reopen the menu to watch progress
- Recent-history rows now use descriptive fallback wording when an old failed attempt never resolved track metadata
- The newest imported/library track stays pinned as `Open Last Finished … ↗` so you can jump straight into Music.app
- The app writes lightweight diagnostics to `~/.local/state/que/menubar.log` so intermittent UI issues can usually be debugged without driving the UI directly

Current menu-bar intake behavior:

- actionable today: YouTube, SoundCloud
- recognized but rejected for now: Spotify, QueUp

Important macOS note:

- Chrome tab fallback needs macOS Automation approval the first time `Que Menu.app` asks Chrome for the active tab URL
- If that access is denied, the app stays usable and shows feedback instead of submitting a job
- If a prior run moved a file into your library folder but Music.app missed it, later runs now retry the Music.app handoff before redownloading
- Harder YouTube videos may need Chrome cookies for metadata lookup too; que now retries that path automatically before giving up
- The packaged `Que Menu.app` now scrubs py2app Python bootstrap env before running `yt-dlp`, so the installed app and terminal app share the same metadata/download behavior
- `uv run que-menu` and `~/Applications/Que Menu.app` share one single-instance lock and the same local daemon, so quit the currently running menu app and restart the daemon after backend changes when you want to test a freshly installed build

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
- `que-menu`: run the menu bar app directly from the terminal

### Config Overview

The config file lives at `~/.config/que/config.toml`.

**Use `que config` if you want a guided terminal flow instead of editing TOML manually!**
The menu bar app's `Settings…` window edits the same file through the daemon.


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
2. The daemon canonicalizes URL intent first: explicit playlist URLs expand, watch URLs stay single-track jobs.
3. The daemon expands playlists into individual tracks and owns queue/job state.
4. The daemon resolves metadata, consults the cache, and checks your local library.
5. It downloads only the tracks that still need work.
6. It tags the downloaded files, moves them into your library folder, and notifies Apple Music.
7. It records history and run metrics so later runs and future clients can reuse the same state.

If your job is queued behind another daemon job, the CLI now stays attached and shows the active job's coarse progress instead of looking stuck.

The menu bar app also uses the daemon for:

- queue and recent-history status
- graphical config reads and writes
- shared intake inspection for clipboard and active-tab URLs

### Diagnostics

- `que list` shows recent per-URL history
- `que runs` shows recent run-level diagnostics
- `que status` shows active/queued jobs in the local daemon
- `Que Menu.app` shows the same queue and recent-track history from the daemon
- Menu-bar diagnostics log: `~/.local/state/que/menubar.log`
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
