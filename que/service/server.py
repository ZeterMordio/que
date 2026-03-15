"""Unix-domain-socket HTTP server for the local que daemon."""
from __future__ import annotations

import fcntl
import json
import os
import socketserver
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..cache import Cache, RunEntry
from ..config import load_config
from .models import JobRequest
from .paths import service_pid_path, service_socket_path, service_state_dir
from .state import DaemonState


class _ThreadingUnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """Threaded HTTP server bound to a Unix domain socket."""

    daemon_threads = True
    allow_reuse_address = True

    def server_close(self) -> None:
        """Close the server socket and remove the filesystem entry."""
        socket_path = getattr(self, "socket_path", None)
        super().server_close()
        if socket_path:
            try:
                Path(socket_path).unlink()
            except FileNotFoundError:
                pass


class _DaemonHTTPHandler(BaseHTTPRequestHandler):
    """HTTP request handler for local daemon endpoints."""

    protocol_version = "HTTP/1.1"
    server_version = "que-daemon/0.1"

    @property
    def state(self) -> DaemonState:
        """Return the shared daemon state."""
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: object) -> None:
        """Silence default request logging."""
        return None

    def do_GET(self) -> None:
        """Handle read-only daemon endpoints."""
        parsed = urlparse(self.path)
        if parsed.path == "/v1/health":
            queue = self.state.queue_snapshot()
            self._write_json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "active_job_id": queue["active"].job_id if queue["active"] else None,
                    "queued_jobs": len(queue["queued"]),
                    "pid": os.getpid(),
                },
            )
            return

        if parsed.path == "/v1/queue":
            queue = self.state.queue_snapshot()
            self._write_json(
                HTTPStatus.OK,
                {
                    "active": queue["active"].to_dict() if queue["active"] else None,
                    "queued": [snapshot.to_dict() for snapshot in queue["queued"]],
                },
            )
            return

        if parsed.path.startswith("/v1/jobs/") and parsed.path.endswith("/events"):
            job_id = parsed.path.split("/")[3]
            self._stream_events(job_id)
            return

        if parsed.path.startswith("/v1/jobs/"):
            job_id = parsed.path.split("/")[3]
            snapshot = self.state.get_job(job_id)
            if snapshot is None:
                self._write_json(HTTPStatus.NOT_FOUND, {"error": "job not found"})
                return
            self._write_json(HTTPStatus.OK, snapshot.to_dict())
            return

        if parsed.path == "/v1/history":
            limit = int(parse_qs(parsed.query).get("limit", ["50"])[0])
            status_filter = parse_qs(parsed.query).get("status", [None])[0]
            rows = _read_history(limit=limit, status_filter=status_filter)
            self._write_json(HTTPStatus.OK, {"rows": rows})
            return

        if parsed.path == "/v1/runs":
            limit = int(parse_qs(parsed.query).get("limit", ["20"])[0])
            rows = [_run_entry_to_dict(entry) for entry in _read_runs(limit=limit)]
            self._write_json(HTTPStatus.OK, {"rows": rows})
            return

        self._write_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        """Handle mutating daemon endpoints."""
        parsed = urlparse(self.path)
        if parsed.path == "/v1/jobs":
            try:
                body = self._read_json()
                request = JobRequest.from_dict(body)
            except ValueError as exc:
                self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return

            snapshot = self.state.submit_job(request)
            self._write_json(HTTPStatus.CREATED, snapshot.to_dict())
            return

        if parsed.path == "/v1/shutdown":
            if not self.state.is_idle():
                self._write_json(
                    HTTPStatus.CONFLICT,
                    {"error": "daemon is busy; wait for the active queue to finish"},
                )
                return

            self._write_json(HTTPStatus.ACCEPTED, {"status": "shutting_down"})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return

        self._write_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def _read_json(self) -> dict[str, Any]:
        """Parse a JSON request body."""
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid json: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("request body must be a JSON object")
        return data

    def _write_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        """Write a JSON response."""
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _stream_events(self, job_id: str) -> None:
        """Stream SSE events for one job until it reaches a terminal state."""
        if self.state.get_job(job_id) is None:
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "job not found"})
            return

        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        cursor = 0
        try:
            while True:
                events, terminal = self.state.wait_for_events(job_id, cursor, timeout=1.0)
                if not events:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    if terminal:
                        break
                    continue

                for event in events:
                    encoded = json.dumps(event.to_dict()).encode("utf-8")
                    self.wfile.write(b"data: " + encoded + b"\n\n")
                    self.wfile.flush()
                cursor += len(events)
                if terminal:
                    break
        except BrokenPipeError:
            return


def _run_entry_to_dict(entry: RunEntry) -> dict[str, Any]:
    """Serialize a cache RunEntry into JSON-safe values."""
    return {
        "run_id": entry.run_id,
        "started_at": entry.started_at.isoformat(),
        "finished_at": entry.finished_at.isoformat() if entry.finished_at else None,
        "run_mode": entry.run_mode,
        "jobs": entry.jobs,
        "total_urls": entry.total_urls,
        "playlist_name": entry.playlist_name,
        "dry_run": entry.dry_run,
        "preflight_seconds": entry.preflight_seconds,
        "download_phase_seconds": entry.download_phase_seconds,
        "total_seconds": entry.total_seconds,
        "queued_downloads": entry.queued_downloads,
        "downloaded_count": entry.downloaded_count,
        "in_library_count": entry.in_library_count,
        "cached_count": entry.cached_count,
        "skipped_count": entry.skipped_count,
        "failed_count": entry.failed_count,
        "download_failed_count": entry.download_failed_count,
        "import_failed_count": entry.import_failed_count,
        "downloaded_bytes": entry.downloaded_bytes,
        "average_download_bytes_per_second": entry.average_download_bytes_per_second,
    }


def _read_history(limit: int, status_filter: str | None) -> list[list[Any]]:
    """Read recent processed URL history using a short-lived cache connection."""
    config = load_config()
    cache = Cache(config.cache_path)
    try:
        return [list(row) for row in cache.recent(limit=limit, status_filter=status_filter)]
    finally:
        cache.close()


def _read_runs(limit: int) -> list[RunEntry]:
    """Read recent run history using a short-lived cache connection."""
    config = load_config()
    cache = Cache(config.cache_path)
    try:
        return cache.recent_runs(limit=limit)
    finally:
        cache.close()


def _acquire_pid_lock(pid_path: Path):
    """Acquire the daemon PID lock or return None if another daemon owns it."""
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    handle = pid_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


def _lock_is_held(pid_path: Path) -> bool:
    """Return True when another daemon currently owns the PID lock."""
    if not pid_path.exists():
        return False
    handle = pid_path.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return False
    finally:
        handle.close()


def serve_forever(
    socket_path: Path | None = None,
    pid_path: Path | None = None,
) -> None:
    """Start the UDS daemon and block forever."""
    state_dir = service_state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    socket_path = socket_path or service_socket_path()
    pid_path = pid_path or service_pid_path()

    if socket_path.exists() and not _lock_is_held(pid_path):
        socket_path.unlink()

    lock_handle = _acquire_pid_lock(pid_path)
    if lock_handle is None:
        raise RuntimeError("que daemon already running")

    if socket_path.exists():
        socket_path.unlink()

    server = _ThreadingUnixHTTPServer(str(socket_path), _DaemonHTTPHandler)
    server.state = DaemonState()  # type: ignore[attr-defined]
    server.socket_path = str(socket_path)  # type: ignore[attr-defined]
    try:
        server.serve_forever()
    finally:
        server.server_close()
        lock_handle.close()

