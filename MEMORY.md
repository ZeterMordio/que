# Memory

- Surprise encountered during Phase 3 daemon work: macOS `AF_UNIX` socket paths are short enough to fail with long temp-directory paths. Prefer short socket paths (for example under `/tmp` in tests) and treat stale/non-socket socket-path errors as "daemon not ready" during client health checks.
