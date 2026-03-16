# Development

Tracked notes for local development, benchmarking, and debugging.

## Local Workflow

Inside the repo, prefer running the source tree directly:

```bash
uv run que --help
uv run que "https://youtube.com/watch?v=..."
uv run que-menu
```

Useful commands:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest
UV_CACHE_DIR=/tmp/uv-cache uv run ruff check .
uv tool install --reinstall .
uv run que daemon --foreground
uv run que status
uv run python -m que.menubar
uv run --with py2app python scripts/build_menubar_app.py
```

Guidelines:

- Use `uv run que ...` while developing so you do not accidentally test a stale installed binary.
- Use `uv run que-menu` or `uv run python -m que.menubar` for menu-bar development from the repo.
- Use plain `que ...` only when you want to smoke-test the installed tool.
- Re-run `./install.sh` or `uv tool install --reinstall .` after CLI changes if you want the global `que` command refreshed.
- The CLI now auto-starts the daemon on demand, so use `uv run que daemon --foreground` when you want server logs and easier Ctrl-C control.
- `uv run --with py2app python scripts/build_menubar_app.py` builds `dist/Que Menu.app` without pulling the full dev toolchain into the bundle.
- Keep the menu-bar refresh timer in both common and `NSEventTrackingRunLoopMode`, otherwise the open dropdown looks frozen until it is reopened.

## Local Daemon

Phase 3 moved queue ownership and history access behind a local daemon.

Important paths:

- socket: `~/.local/state/que/daemon.sock`
- pid/lock: `~/.local/state/que/daemon.pid`
- cache DB: `~/.local/share/que/cache.db`

Useful flows:

```bash
uv run que daemon --foreground
uv run que "https://youtube.com/watch?v=..."
uv run que status
uv run que runs --limit 10
uv run que daemon stop
uv run que-menu
```

Notes:

- the daemon owns one active job at a time and queues later submissions FIFO
- active queue state is intentionally ephemeral; restart drops in-flight work
- completed history survives because it still lives in SQLite
- menu-bar config and intake inspection also go through the daemon; avoid duplicating those rules in UI code
- YouTube watch URLs with `list=` / `index=` context are canonicalized back to a single track; only explicit `/playlist?list=...` URLs expand
- repeated identical active/queued submissions deduplicate at the daemon instead of creating queue spam
- harder YouTube metadata lookups may require Chrome cookies even before download starts; retry that in the resolver before treating metadata as a hard failure

## Benchmark Mode

`--benchmark` exists for repeatable download-engine measurements, not end-to-end user timing.

What benchmark mode skips:

- metadata resolution
- Chrome-cookie retry after anonymous YouTube anti-bot metadata failures
- URL cache reads and writes
- library matching
- tagging
- Apple Music import

What benchmark mode still does:

- download each eligible URL
- record run and per-item metrics
- clean up throwaway staging files after each track

Typical comparison flow:

```bash
uv run que --benchmark --jobs 1 "<playlist-url>"
uv run que --benchmark --jobs 2 "<playlist-url>"
uv run que --benchmark --jobs 3 "<playlist-url>"
que runs
```

Interpretation:

- use benchmark mode when comparing downloader throughput or worker counts
- use normal `que ...` runs when measuring the full user-visible pipeline

## Performance Metrics And Cache

The SQLite DB lives at `~/.local/share/que/cache.db`.

Main tables:

- `processed_urls`: last-known result per URL for fast skip behavior on repeat runs
- `processing_runs`: aggregate run metrics
- `processing_run_items`: per-track metrics inside a run

Useful fields in `processing_runs`:

- `run_mode`
- `jobs`
- `preflight_seconds`
- `download_phase_seconds`
- `total_seconds`
- `queued_downloads`
- `downloaded_count`
- `failed_count`
- `download_failed_count`
- `import_failed_count`
- `downloaded_bytes`
- `average_download_bytes_per_second`

Useful fields in `processing_run_items`:

- `item_index`
- `status`
- `queue_wait_seconds`
- `download_seconds`
- `tag_seconds`
- `import_seconds`
- `total_item_seconds`
- `file_size_bytes`
- `download_bytes_per_second`
- `failure_stage`
- `worker_name`

Quick inspection:

```bash
que list
que runs
que status
sqlite3 ~/.local/share/que/cache.db
```

## Debugging And Perf Notes

Downloader behavior:

- Fast path: `yt-dlp` runs without browser cookies and prefers audio-only formats.
- Fallback path: if the first attempt fails, `que` retries with Chrome cookies.
- For `m4a`, the downloader prefers existing M4A/AAC streams before falling back to broader audio selection.

Concurrency behavior:

- `--jobs 1` keeps the responsive serial path for normal runs.
- `--jobs > 1` parallelizes download work only.
- Tagging, Apple Music import, and cache writes stay serialized.
- Parallel workers use isolated staging directories.
- The daemon itself still runs only one queued job at a time.

When investigating slow runs:

1. Compare `--benchmark --jobs 1` vs `--benchmark --jobs N`.
2. Check `que runs` for download rate, failures, and queued download counts.
3. If benchmark is fast but normal runs are slow, the overhead is outside raw downloading.
4. If cookie fallback is happening often, inspect the specific `yt-dlp` failure mode.

When investigating daemon issues:

1. Start with `uv run que daemon --foreground`.
2. In another shell, submit a job and watch `uv run que status`.
3. If a direct CLI run looks stuck, first check whether it is queued behind another daemon job; the CLI now prints the active job's coarse progress while waiting.
4. If the daemon looks stuck but `que runs` is still healthy, inspect the active job's last streamed log line.

When investigating menu-bar issues:

1. Start with `uv run que daemon --foreground`.
2. Launch `uv run que-menu` or `uv run python -m que.menubar`.
3. Right click the menu icon with a supported URL in the clipboard first, then test the Chrome fallback path.
4. If active-tab fallback fails, check macOS Privacy & Security > Automation for Google Chrome access.

## Related Docs

- [ARCHITECTURE.md](ARCHITECTURE.md)
- [CHANGELOG.md](CHANGELOG.md)
