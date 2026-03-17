# que

`que` is a macOS menu bar app and CLI for bringing YouTube tracks and playlists into Apple Music without redownloading songs you already have.

For most people, `Que Menu.app` is the easiest way to use it: left click for status, history, and settings, right click to quickly download and sync from anywhere. The CLI is still there when you want direct control.

## What It Does

- Ships `Que Menu.app` so you can left click for status/history/settings or right click to download and sync
- Accepts a YouTube track or playlist URL from the clipboard, an active Chrome tab, or the command line
- Auto-starts a local daemon so the menu bar app and CLI share one source of truth for queue state, history, and live progress
- Checks your local library before downloading, so obvious duplicates are skipped
- Downloads missing tracks, tags them, and imports them into Apple Music
- Can add newly imported tracks to a named Apple Music playlist

## Requirements

- macOS
- Apple Music
- Python 3.11+
- Homebrew recommended for the install flow

Optional but useful:

- Google Chrome, for the menu bar app's active-tab fallback on right click and occasional `yt-dlp` cookie fallback on harder-to-download videos

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

That is usually enough. The install script handles `uv`, `yt-dlp`, `que`, and `Que Menu.app` for you. Most users can launch the menu bar app right away and start from there.

## Quickstart

1. Launch `Que Menu.app` from `~/Applications`.
2. Copy a YouTube track or playlist URL, or leave Chrome frontmost on one.
3. Right click the menu bar icon to start a sync immediately.
4. Left click any time to watch status, open the latest imported track, or change settings.

If you prefer the CLI, the fastest path is still:

```bash
que
```

`que` reads the clipboard, checks your library, downloads missing tracks, and imports them into Apple Music.

If you prefer not to use the clipboard, pass the URL directly:

```bash
que "https://youtube.com/playlist?list=..."
```

Important YouTube behavior:

- `watch?v=...` and `youtu.be/...` URLs are treated as single tracks, even when YouTube adds `list=` / `index=` mix context
- use an explicit `/playlist?list=...` URL when you want the whole playlist expanded

## Everyday Usage

Most people will use the menu bar app day to day:

```bash
open ~/Applications/Que\ Menu.app
```

Right click to download and sync right away. Left click for status, history, `Open Last Finished … ↗`, and `Settings…`.

Use the CLI when you want direct control:

```bash
que                          # From clipboard
que <URL>                    # Direct URL
que --playlist "Blues" <URL> # Add imported tracks to a playlist directly
que --dry-run <URL>          # Preview download first
que config                   # Terminal config helper
que-menu                     # Launch the menu bar app from a terminal
```

## Menu Bar App

`Que Menu.app` is the default interface. It uses the same queue, history, and config as the CLI.

- Left click for status, recent tracks, settings, and `Open Last Finished … ↗`
- Right click to start the default sync flow without opening the menu
- If the clipboard has no actionable URL and Google Chrome is frontmost, right click falls back to the active tab URL
- Active jobs stay live in the open dropdown, including current track, phase, progress, queue, and ETA
- Diagnostics log: `~/.local/state/que/menubar.log`

Current menu-bar intake behavior:

- actionable today: YouTube, SoundCloud
- recognized but rejected for now: Spotify, QueUp

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

macOS notes:

- Chrome tab fallback needs macOS Automation approval the first time `Que Menu.app` asks Chrome for the active tab URL
- If that access is denied, the app stays usable and shows feedback instead of submitting a job
- If a prior run moved a file into your library folder but Apple Music missed it, later runs now retry the Apple Music handoff before redownloading
- Harder YouTube videos may need Chrome cookies for metadata lookup too; `que` now retries that path automatically before giving up
- The packaged `Que Menu.app` now scrubs py2app Python bootstrap env before running `yt-dlp`, so the installed app and terminal app share the same metadata/download behavior
- `uv run que-menu` and `~/Applications/Que Menu.app` share one single-instance lock and the same local daemon, so quit the currently running menu app and restart the daemon after backend changes when you want to test a freshly installed build

## Future Enhancements

- **Chrome extension:** lightweight companion surface for one-click downloads.
- Add Spotify & SoundCloud support.

## Developer Docs

- [DEVELOPMENT.md](DEVELOPMENT.md)
- [ARCHITECTURE.md](ARCHITECTURE.md)
- [CHANGELOG.md](CHANGELOG.md)
