"""In-memory queue state and event fanout for the local que daemon."""
from __future__ import annotations

import threading
import uuid
from collections import deque
from dataclasses import replace
from datetime import datetime
from typing import Any

from .executor import execute_job
from .models import TERMINAL_JOB_STATES, JobRequest, JobSnapshot, JobSubmission, ServiceEvent
from .progress import initialize_snapshot_progress, project_pipeline_event


class _JobRuntime:
    """Mutable runtime state for one daemon job."""

    def __init__(self, request: JobRequest, snapshot: JobSnapshot) -> None:
        self.request = request
        self.snapshot = snapshot
        self.events: list[ServiceEvent] = []
        self.condition = threading.Condition()


class DaemonState:
    """Owns queue scheduling, in-memory snapshots, and job event fanout."""

    def __init__(self, max_completed_jobs: int = 50) -> None:
        self._lock = threading.RLock()
        self._jobs: dict[str, _JobRuntime] = {}
        self._queue: deque[str] = deque()
        self._active_job_id: str | None = None
        self._max_completed_jobs = max_completed_jobs

    def submit_job(self, request: JobRequest) -> JobSubmission:
        """Queue a new job or reuse an identical active/queued job.

        Args:
            request: Canonical daemon-backed job request.

        Returns:
            Submission result containing the job snapshot plus dedupe metadata.
        """
        request.validate()
        fingerprint = request.fingerprint()

        with self._lock:
            existing = self._find_duplicate_job_locked(fingerprint)
            if existing is not None:
                return JobSubmission(snapshot=replace(existing.snapshot), deduplicated=True)

            job_id = uuid.uuid4().hex[:12]
            snapshot = JobSnapshot(
                job_id=job_id,
                state="queued",
                position=0,
                total_urls=len(request.urls),
                run_mode="benchmark" if request.benchmark else "normal",
            )
            initialize_snapshot_progress(snapshot)
            runtime = _JobRuntime(request=request, snapshot=snapshot)
            self._jobs[job_id] = runtime
            self._queue.append(job_id)
            self._recompute_positions_locked()
            self._append_event_locked(
                runtime,
                ServiceEvent(
                    type="job_queued",
                    job_id=job_id,
                    message=f"[dim]Queued job {job_id}[/dim]",
                    payload={"position": runtime.snapshot.position},
                ),
            )
            self._maybe_start_next_locked()
            return JobSubmission(snapshot=replace(runtime.snapshot), deduplicated=False)

    def get_job(self, job_id: str) -> JobSnapshot | None:
        """Return the current snapshot for a job."""
        with self._lock:
            runtime = self._jobs.get(job_id)
            if runtime is None:
                return None
            return replace(runtime.snapshot)

    def queue_snapshot(self) -> dict[str, Any]:
        """Return active and queued job snapshots."""
        with self._lock:
            active = (
                replace(self._jobs[self._active_job_id].snapshot)
                if self._active_job_id and self._active_job_id in self._jobs
                else None
            )
            queued = [
                replace(self._jobs[job_id].snapshot)
                for job_id in self._queue
                if job_id != self._active_job_id
            ]
            return {"active": active, "queued": queued}

    def is_idle(self) -> bool:
        """Return True when no job is active or waiting."""
        with self._lock:
            return self._active_job_id is None and not self._queue

    def wait_for_events(
        self,
        job_id: str,
        cursor: int,
        timeout: float = 1.0,
    ) -> tuple[list[ServiceEvent], bool]:
        """Block until events are available or the job reaches a terminal state."""
        runtime = self._jobs.get(job_id)
        if runtime is None:
            raise KeyError(job_id)

        with runtime.condition:
            if cursor >= len(runtime.events) and runtime.snapshot.state not in TERMINAL_JOB_STATES:
                runtime.condition.wait(timeout=timeout)
            events = runtime.events[cursor:]
            terminal = runtime.snapshot.state in TERMINAL_JOB_STATES
            return list(events), terminal

    def _append_event_locked(self, runtime: _JobRuntime, event: ServiceEvent) -> None:
        """Append an event and wake any SSE waiters."""
        runtime.snapshot.last_message = event.message or runtime.snapshot.last_message
        with runtime.condition:
            runtime.events.append(event)
            runtime.condition.notify_all()

    def _recompute_positions_locked(self) -> None:
        """Recalculate queue positions for waiting jobs."""
        for position, job_id in enumerate(self._queue, 1):
            runtime = self._jobs[job_id]
            if runtime.snapshot.state != "queued":
                continue
            initialize_snapshot_progress(runtime.snapshot)
            if runtime.snapshot.position == position:
                continue
            runtime.snapshot.position = position
            self._append_event_locked(
                runtime,
                ServiceEvent(
                    type="queue_position_updated",
                    job_id=job_id,
                    payload={"position": position},
                ),
            )

    def _maybe_start_next_locked(self) -> None:
        """Start the next queued job when the daemon is idle."""
        if self._active_job_id is not None or not self._queue:
            return

        job_id = self._queue.popleft()
        runtime = self._jobs[job_id]
        self._active_job_id = job_id
        runtime.snapshot.state = "running"
        runtime.snapshot.position = 0
        runtime.snapshot.started_at = datetime.now().isoformat()
        runtime.snapshot.detail = "Starting job"
        runtime.snapshot.phase = "preflight"
        initialize_snapshot_progress(runtime.snapshot)
        self._append_event_locked(
            runtime,
            ServiceEvent(
                type="job_started",
                job_id=job_id,
                message=f"[bold]Job {job_id} started[/bold]",
            ),
        )

        thread = threading.Thread(
            target=self._run_job,
            args=(job_id,),
            name=f"que-job-{job_id}",
            daemon=True,
        )
        thread.start()
        self._recompute_positions_locked()

    def _run_job(self, job_id: str) -> None:
        """Execute one queued job, update its snapshot, then advance the queue."""
        with self._lock:
            runtime = self._jobs[job_id]

        try:
            result = execute_job(
                runtime.request,
                event_handler=lambda event_type, message, payload: self._handle_pipeline_event(
                    job_id,
                    event_type,
                    message,
                    payload,
                ),
            )
            with self._lock:
                runtime.snapshot.state = "completed"
                runtime.snapshot.finished_at = (
                    runtime.snapshot.finished_at or datetime.now().isoformat()
                )
                runtime.snapshot.run_id = result.run_id
                runtime.snapshot.run_mode = result.run_mode
                self._append_event_locked(
                    runtime,
                    ServiceEvent(
                        type="job_finished",
                        job_id=job_id,
                        payload={
                            "run_id": result.run_id,
                            "stats": result.stats,
                            "total_seconds": result.total_seconds,
                        },
                    ),
                )
        except Exception as exc:
            with self._lock:
                runtime.snapshot.state = "failed"
                runtime.snapshot.finished_at = (
                    runtime.snapshot.finished_at or datetime.now().isoformat()
                )
                project_pipeline_event(
                    runtime.snapshot,
                    event_type="job_failed",
                    message=f"Job failed: {exc}",
                    payload={"error": str(exc)},
                )
                self._append_event_locked(
                    runtime,
                    ServiceEvent(
                        type="job_failed",
                        job_id=job_id,
                        message=f"[red]Job failed: {exc}[/red]",
                        payload={"error": str(exc)},
                    ),
                )
        finally:
            with self._lock:
                if runtime.snapshot.finished_at is None:
                    runtime.snapshot.finished_at = datetime.now().isoformat()
                if self._active_job_id == job_id:
                    self._active_job_id = None
                self._prune_completed_locked()
                self._maybe_start_next_locked()

    def _handle_pipeline_event(
        self,
        job_id: str,
        event_type: str,
        message: str | None,
        payload: dict[str, Any],
    ) -> None:
        """Update snapshot counters and fan out one pipeline event."""
        with self._lock:
            runtime = self._jobs[job_id]
            snapshot = runtime.snapshot

            if event_type == "expanded_urls_ready":
                snapshot.total_urls = int(payload.get("total_urls", snapshot.total_urls))
            elif event_type == "preflight_finished":
                snapshot.queued_downloads = int(payload.get("queued_downloads", 0))
            elif event_type == "item_report":
                report = payload.get("report", {})
                status = report.get("status")
                snapshot.processed_count += 1
                if status == "downloaded":
                    snapshot.downloaded_count += 1
                elif status == "benchmarked":
                    snapshot.benchmarked_count += 1
                elif status == "in_library":
                    snapshot.in_library_count += 1
                elif status == "cached":
                    snapshot.cached_count += 1
                elif status == "skipped":
                    snapshot.skipped_count += 1
                elif status == "failed":
                    snapshot.failed_count += 1
            elif event_type == "summary":
                snapshot.run_id = payload.get("run_id")
                snapshot.finished_at = payload.get("finished_at", snapshot.finished_at)

            project_pipeline_event(
                snapshot,
                event_type=event_type,
                message=message,
                payload=payload,
            )

            self._append_event_locked(
                runtime,
                ServiceEvent(
                    type=event_type,
                    job_id=job_id,
                    message=message,
                    payload=payload,
                ),
            )

    def _prune_completed_locked(self) -> None:
        """Keep a bounded set of completed jobs in memory."""
        completed_job_ids = [
            job_id
            for job_id, runtime in self._jobs.items()
            if runtime.snapshot.state in TERMINAL_JOB_STATES
        ]
        if len(completed_job_ids) <= self._max_completed_jobs:
            return

        completed_job_ids.sort(
            key=lambda job_id: self._jobs[job_id].snapshot.finished_at or "",
        )
        excess = len(completed_job_ids) - self._max_completed_jobs
        for job_id in completed_job_ids[:excess]:
            if job_id == self._active_job_id or job_id in self._queue:
                continue
            self._jobs.pop(job_id, None)

    def _find_duplicate_job_locked(
        self,
        fingerprint: tuple[Any, ...],
    ) -> _JobRuntime | None:
        """Return a matching active or queued runtime for exact dedupe checks."""
        active_and_queued = list(self._queue)
        if self._active_job_id and self._active_job_id not in active_and_queued:
            active_and_queued.insert(0, self._active_job_id)

        for job_id in active_and_queued:
            runtime = self._jobs.get(job_id)
            if runtime is None:
                continue
            if runtime.snapshot.state not in {"queued", "running"}:
                continue
            if runtime.request.fingerprint() == fingerprint:
                return runtime
        return None
