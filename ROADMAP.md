# Roadmap

`que` stays macOS-first, CLI-first, and test-driven. This roadmap is ordered intentionally: each phase builds foundations for the next so we avoid duplicating logic across clients and integrations.

## Principles

- Extensive testing is mandatory in every phase.
- Build shared foundations before adding new clients.
- Keep UI surfaces thin; core behavior belongs in the CLI/service layer.
- AI-powered search is additive and separate from the current lightweight download-time fuzzy matching flow.

## Phase 1: Core CLI Usability

Focus: make the existing CLI more configurable and more useful in daily Apple Music workflows.

- Build a strong regression test baseline for config loading, metadata resolution, library matching, cache behavior, and import flow.
- Add `que config` to view and edit config from the terminal.
- Add `--playlist` to place imported tracks into a named Apple Music playlist.

Exit criteria:

- Stable and documented config workflow.
- Playlist targeting works without breaking the current import path.
- Regression coverage exists for the main CLI decision paths.

## Phase 2: Core Engine Throughput

Focus: improve speed while keeping the current workflow reliable.

- Add parallel downloads.
- Add queue/progress/error-handling behavior needed to make concurrency safe.
- Keep caching, tagging, and import behavior correct under concurrent execution.

Exit criteria:

- Parallelism improves throughput without breaking cache/import/tagging behavior.
- Per-track failures stay isolated and visible.
- Tests cover concurrency-sensitive behavior.

## Phase 3: Shared Local Service Layer

Focus: create a single local backend that all future clients can use.

- Add a local background daemon/API for queue state, cache access, job status, and event streaming.
- Make the CLI talk to this shared backend rather than every future client implementing its own logic.
- Use this as the foundation for hotkeys, menu bar actions, browser integrations, and future clients.

Exit criteria:

- CLI and daemon communicate cleanly through a stable local interface.
- Queue/state/status have one source of truth.
- Tests cover daemon-backed core flows and failure handling.

## Phase 4: Quick-Access Clients

Focus: make `que` accessible from lightweight visual surfaces without duplicating backend logic.

- Build a menu bar app first for quick status, one-click syncs, and visual feedback.
- Build a Chrome extension second for fast capture/sync of the current YouTube song or playlist.
- Keep both as thin clients on top of the local daemon/API.

Exit criteria:

- Menu bar app and extension both operate against shared backend behavior.
- One-click sync/status actions are reliable.
- Client-side tests cover integration boundaries and basic UX flows.

## Phase 5: Source Expansion

Focus: ingest from more platforms without forking the core pipeline.

- Add Spotify support via `spotdl`.
- Add SoundCloud support.
- Keep source-specific logic behind clean interfaces so download/tag/import behavior stays unified.

Exit criteria:

- New sources feed the same core queue/import flow.
- Source-specific failures are isolated and debuggable.
- Tests cover per-source ingest behavior and shared pipeline regressions.

## Phase 6: Intelligent Library Search

Focus: add a separate, semantically smart discovery tool for finding songs already buried in local libraries.

- Build a new intelligent library search feature using semantic retrieval plus BM25/text retrieval.
- Support lyric snippets, vague remembered words, vibe/feel descriptions, and other partial-memory queries.
- Search across local libraries such as Apple Music and Spotify-backed local catalogs.
- Keep this separate from playlist download/import matching.

Important constraint:

This phase does **not** replace the current fuzzy matching used during playlist download/import. That path must stay fast and lightweight for large playlists. The intelligent search feature is for "find the old song I vaguely remember," not "decide whether to skip this download."

Exit criteria:

- Indexing and search architecture are explicit and documented.
- Search works well for fragmentary and semantic queries.
- Tests cover indexing, retrieval quality checks, and failure modes.

## Ordering Note

The order above is deliberate: first strengthen the CLI, then improve throughput, then create a shared local service, then add lightweight clients, then expand sources, and finally add a separate intelligent search experience on top of a stronger foundation.
