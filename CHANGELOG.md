# Changelog

## Unreleased

- Added a `que config` terminal workflow for viewing, initializing, and editing config.
- Added an interactive `que config` helper wizard for quick terminal-side setup.
- Added `--playlist` support for placing imported tracks into a named Apple Music playlist.
- Normalized import config around `import.mode = "move_then_music"` and removed the unsupported `fallback_to_folder` path from the user-facing workflow.
- Added Phase 1 regression tests around config handling, importer behavior, CLI flow, resolver fallbacks, and library matching/indexing.
- Updated README usage/config examples for the new Phase 1 workflow.
