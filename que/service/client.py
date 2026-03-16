"""Client helpers for talking to the local que daemon over a Unix socket."""
from __future__ import annotations

import http.client
import json
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from ..cache import RunEntry
from .models import JobRequest, JobSnapshot, JobSubmission, ServiceEvent
from .paths import service_socket_path


class _UnixHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection variant that connects over a Unix domain socket."""

    def __init__(self, socket_path: Path, timeout: float = 5.0) -> None:
        super().__init__("localhost", timeout=timeout)
        self._socket_path = str(socket_path)

    def connect(self) -> None:
        """Open the Unix domain socket connection."""
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._socket_path)


class DaemonClient:
    """Thin JSON/SSE client for the local que daemon."""

    def __init__(
        self,
        socket_path: Path | None = None,
        *,
        autostart: bool = True,
        startup_timeout_seconds: float = 5.0,
    ) -> None:
        self.socket_path = socket_path or service_socket_path()
        self.autostart = autostart
        self.startup_timeout_seconds = startup_timeout_seconds

    def health(self) -> dict[str, Any]:
        """Return the daemon health payload."""
        return self._request_json("GET", "/v1/health", autostart=self.autostart)

    def submit_job(self, request: JobRequest) -> JobSubmission:
        """Submit a new job to the daemon."""
        payload = self._request_json(
            "POST",
            "/v1/jobs",
            body=request.to_dict(),
            expected_statuses={200, 201},
            autostart=self.autostart,
        )
        return JobSubmission.from_dict(payload)

    def get_job(self, job_id: str) -> JobSnapshot:
        """Fetch a single job snapshot."""
        payload = self._request_json("GET", f"/v1/jobs/{job_id}", autostart=self.autostart)
        return JobSnapshot.from_dict(payload)

    def get_queue(self) -> dict[str, Any]:
        """Fetch active and queued job snapshots."""
        payload = self._request_json("GET", "/v1/queue", autostart=self.autostart)
        return {
            "active": JobSnapshot.from_dict(payload["active"]) if payload["active"] else None,
            "queued": [JobSnapshot.from_dict(item) for item in payload["queued"]],
        }

    def recent(self, limit: int = 50, status_filter: str | None = None):
        """Fetch recent processed URL history."""
        query = f"/v1/history?limit={limit}"
        if status_filter:
            query += f"&status={status_filter}"
        payload = self._request_json("GET", query, autostart=self.autostart)
        return [tuple(row) for row in payload["rows"]]

    def recent_runs(self, limit: int = 20) -> list[RunEntry]:
        """Fetch recent run summaries."""
        payload = self._request_json(
            "GET",
            f"/v1/runs?limit={limit}",
            autostart=self.autostart,
        )
        return [
            RunEntry(
                run_id=row["run_id"],
                started_at=datetime.fromisoformat(row["started_at"]),
                finished_at=(
                    datetime.fromisoformat(row["finished_at"])
                    if row["finished_at"]
                    else None
                ),
                run_mode=row["run_mode"],
                jobs=row["jobs"],
                total_urls=row["total_urls"],
                playlist_name=row["playlist_name"],
                dry_run=bool(row["dry_run"]),
                preflight_seconds=row["preflight_seconds"],
                download_phase_seconds=row["download_phase_seconds"],
                total_seconds=row["total_seconds"],
                queued_downloads=row["queued_downloads"],
                downloaded_count=row["downloaded_count"],
                in_library_count=row["in_library_count"],
                cached_count=row["cached_count"],
                skipped_count=row["skipped_count"],
                failed_count=row["failed_count"],
                download_failed_count=row["download_failed_count"],
                import_failed_count=row["import_failed_count"],
                downloaded_bytes=row["downloaded_bytes"],
                average_download_bytes_per_second=row["average_download_bytes_per_second"],
            )
            for row in payload["rows"]
        ]

    def get_config(self) -> dict[str, Any]:
        """Fetch the active daemon-backed config payload."""
        return self._request_json("GET", "/v1/config", autostart=self.autostart)

    def update_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Persist a config update through the daemon."""
        return self._request_json(
            "PUT",
            "/v1/config",
            body=payload,
            autostart=self.autostart,
        )

    def inspect_input(
        self,
        *,
        text: str | None = None,
        candidates: list[str] | None = None,
    ) -> dict[str, Any]:
        """Inspect intake text or explicit URLs through the daemon."""
        return self._request_json(
            "POST",
            "/v1/intake/inspect",
            body={"text": text, "candidates": candidates},
            autostart=self.autostart,
        )

    def stop(self) -> dict[str, Any]:
        """Request a clean idle shutdown from the daemon."""
        try:
            return self._request_json(
                "POST",
                "/v1/shutdown",
                body={},
                expected_statuses={202},
                autostart=False,
            )
        except RuntimeError as exc:
            if str(exc) != "que daemon socket not found":
                raise
            # Reason: shutdown can win the race and remove the Unix socket
            # before the client reads the HTTP response. Missing socket here
            # still means the daemon stopped successfully.
            return {"status": "shutting_down"}

    def stream_events(self, job_id: str) -> Iterator[ServiceEvent]:
        """Yield SSE events for one job."""
        self._ensure_running()
        connection = _UnixHTTPConnection(self.socket_path, timeout=self.startup_timeout_seconds)
        connection.request("GET", f"/v1/jobs/{job_id}/events")
        response = connection.getresponse()
        if response.status != 200:
            payload = response.read().decode("utf-8")
            raise RuntimeError(f"event stream failed: {response.status} {payload}")

        try:
            buffer: list[str] = []
            while True:
                raw_line = response.fp.readline()
                if not raw_line:
                    break
                line = raw_line.decode("utf-8").rstrip("\n")
                if line.startswith(":"):
                    continue
                if line.startswith("data: "):
                    buffer.append(line[6:])
                    continue
                if line == "":
                    if not buffer:
                        continue
                    payload = json.loads("".join(buffer))
                    buffer.clear()
                    yield ServiceEvent.from_dict(payload)
        finally:
            response.close()
            connection.close()

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        expected_statuses: set[int] | None = None,
        autostart: bool,
    ) -> dict[str, Any]:
        """Issue one JSON request and decode the JSON response."""
        if autostart:
            self._ensure_running()

        encoded_body = None
        headers = {}
        if body is not None:
            encoded_body = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(encoded_body))

        response = None
        raw = ""
        for attempt in range(2):
            connection = _UnixHTTPConnection(
                self.socket_path,
                timeout=self.startup_timeout_seconds,
            )
            try:
                connection.request(method, path, body=encoded_body, headers=headers)
                response = connection.getresponse()
                raw = response.read().decode("utf-8")
                break
            except FileNotFoundError as exc:
                raise RuntimeError("que daemon socket not found") from exc
            except OSError as exc:
                if attempt == 0:
                    time.sleep(0.05)
                    continue
                raise RuntimeError("que daemon socket is not ready") from exc
            finally:
                connection.close()

        if response is None:
            raise RuntimeError("que daemon socket is not ready")

        payload = json.loads(raw or "{}")
        expected_statuses = expected_statuses or {200}
        if response.status not in expected_statuses:
            message = payload.get("error") if isinstance(payload, dict) else raw
            raise RuntimeError(message or f"daemon request failed with status {response.status}")
        return payload

    def _ensure_running(self) -> None:
        """Auto-start the daemon when it is not reachable yet."""
        try:
            self._request_json("GET", "/v1/health", autostart=False)
            return
        except Exception as exc:
            if not self.autostart:
                raise RuntimeError("que daemon is not running") from exc

        subprocess.Popen(
            [sys.executable, "-m", "que.main", "daemon", "--foreground"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        deadline = time.monotonic() + self.startup_timeout_seconds
        while time.monotonic() < deadline:
            try:
                self._request_json("GET", "/v1/health", autostart=False)
                return
            except Exception:
                time.sleep(0.1)
        raise RuntimeError("timed out waiting for que daemon to start")
