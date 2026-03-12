# Changelog

## Unreleased

- Reworked the README into a user-facing landing page and added tracked root-level `DEVELOPMENT.md` and `ARCHITECTURE.md` docs for developer and agent-facing detail.
- Added Phase 2 parallel downloads with `--jobs`, sequential preflight, and serialized tag/import/cache commits.
- Added concurrency regression tests for ordered results, staging isolation, and main-thread cache writes.
- Added run-level and per-track performance metrics in the SQLite cache DB, plus `que runs` for quick inspection.
- Added `--benchmark` mode for repeatable same-URL throughput testing without cache, library checks, tagging, or Apple Music import.
- Clarified benchmark docs/help so `--benchmark` is explicitly download-engine-only, not end-to-end sync timing.
- Reduced parallel startup spikes by staggering the first worker wave and limiting each ExtractAudio ffmpeg postprocessor to one thread.
- Restored the old responsive single-worker flow so `--jobs 1` no longer waits for full-playlist preflight before the first download starts.
- Limited the ExtractAudio ffmpeg thread cap to multi-worker runs only; single-worker runs now use yt-dlp/ffmpeg defaults again.
- Pinned yt-dlp to audio-only format selection instead of relying on current default format selection, which could otherwise download full video+audio streams before extraction.
- Stopped sending Chrome browser cookies on the default download path; que now retries with cookies only as a fallback when the initial yt-dlp attempt fails.
- Added a `que config` terminal workflow for viewing, initializing, and editing config.
- Added an interactive `que config` helper wizard for quick terminal-side setup.
- Added `--playlist` support for placing imported tracks into a named Apple Music playlist.
- Normalized import config around `import.mode = "move_then_music"` and removed the unsupported `fallback_to_folder` path from the user-facing workflow.
- Added Phase 1 regression tests around config handling, importer behavior, CLI flow, resolver fallbacks, and library matching/indexing.
- Updated README usage/config examples for the new Phase 1 workflow.
