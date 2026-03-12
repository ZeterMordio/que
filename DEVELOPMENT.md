# Development

Tracked notes for local development, benchmarking, and debugging.

## Local Workflow

Inside the repo, prefer running the source tree directly:

```bash
uv run que --help
uv run que "https://youtube.com/watch?v=..."
```

Useful commands:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest
UV_CACHE_DIR=/tmp/uv-cache uv run ruff check .
uv tool install --reinstall .
```

Guidelines:

- Use `uv run que ...` while developing so you do not accidentally test a stale installed binary.
- Use plain `que ...` only when you want to smoke-test the installed tool.
- Re-run `./install.sh` or `uv tool install --reinstall .` after CLI changes if you want the global `que` command refreshed.

## Benchmark Mode

`--benchmark` exists for repeatable download-engine measurements, not end-to-end user timing.

What benchmark mode skips:

- metadata resolution
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

When investigating slow runs:

1. Compare `--benchmark --jobs 1` vs `--benchmark --jobs N`.
2. Check `que runs` for download rate, failures, and queued download counts.
3. If benchmark is fast but normal runs are slow, the overhead is outside raw downloading.
4. If cookie fallback is happening often, inspect the specific `yt-dlp` failure mode.

## Related Docs

- [ARCHITECTURE.md](ARCHITECTURE.md)
- [CHANGELOG.md](CHANGELOG.md)
