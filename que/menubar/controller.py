"""Thin controller logic for the macOS `que` menu-bar app."""
from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..clipboard import read_clipboard
from ..service.client import DaemonClient
from ..service.models import TERMINAL_JOB_STATES, JobRequest
from .activity import (
    TrackTarget,
    history_status_label,
    is_openable_status,
    latest_open_track_from_reports,
)
from .diagnostics import log_diagnostic

GOOGLE_CHROME_BUNDLE_ID = "com.google.Chrome"
_RICH_TAG_RE = re.compile(r"\[/?[^\]]+\]")
_PHASE_ICONS = {
    "queued": "…",
    "preflight": "⌕",
    "checking_library": "♫",
    "preparing_download": "↓",
    "downloading": "↓",
    "importing": "↗",
    "finishing": "✓",
    "completed": "✓",
    "failed": "!",
}
_PHASE_LABELS = {
    "queued": "Waiting in Queue",
    "preflight": "Inspecting Input",
    "checking_library": "Matching Library",
    "preparing_download": "Preparing Download",
    "downloading": "Downloading",
    "importing": "Importing to Music",
    "finishing": "Finalizing",
    "completed": "Finished",
    "failed": "Failed",
}


@dataclass(frozen=True)
class HistoryItem:
    """Small recent-history row for menu rendering."""

    status: str
    status_label: str
    row_label: str
    artist: str
    title: str
    relative_time: str
    openable: bool = False


@dataclass(frozen=True)
class CompletionNotice:
    """Pending user notification emitted after one terminal job result."""

    title: str
    message: str
    success: bool
    track: TrackTarget | None = None


@dataclass(frozen=True)
class MenuSnapshot:
    """Current menu render model."""

    title: str
    detail: str
    active_job_id: str | None
    active_phase: str | None
    queue_length: int
    history: list[HistoryItem] = field(default_factory=list)
    status_lines: list[str] = field(default_factory=list)
    primary_track: TrackTarget | None = None


@dataclass(frozen=True)
class SubmissionOutcome:
    """Result of a one-click sync attempt."""

    submitted: bool
    message: str
    job_id: str | None = None
    urls: list[str] = field(default_factory=list)


class MenuBarController:
    """Own the menu-bar app's thin-client behavior."""

    def __init__(
        self,
        client: DaemonClient | None = None,
        *,
        read_clipboard_text: Callable[[], str] = read_clipboard,
        frontmost_bundle_id: Callable[[], str | None],
        read_chrome_url: Callable[[], str | None],
        poll_interval_seconds: float = 1.0,
    ) -> None:
        """Create a controller with injectable system hooks.

        Args:
            client: Daemon API client.
            read_clipboard_text: Clipboard text provider.
            frontmost_bundle_id: Provider for the frontmost app bundle identifier.
            read_chrome_url: Provider for the Google Chrome active-tab URL.
            poll_interval_seconds: Background polling interval for submitted jobs.
        """
        self.client = client or DaemonClient()
        self.read_clipboard_text = read_clipboard_text
        self.frontmost_bundle_id = frontmost_bundle_id
        self.read_chrome_url = read_chrome_url
        self.poll_interval_seconds = poll_interval_seconds
        self._lock = threading.RLock()
        self._last_feedback = "Idle"
        self._watching_jobs: set[str] = set()
        self._pending_notifications: list[CompletionNotice] = []

    def refresh_snapshot(self) -> MenuSnapshot:
        """Fetch daemon state and build a rich menu view model.

        Returns:
            Menu snapshot for immediate UI rendering.
        """
        queue = self.client.get_queue()
        history_rows = self.client.recent(limit=3)
        active = queue["active"]
        queued = queue["queued"]

        history = [_history_item_from_row(row) for row in history_rows[:3]]
        primary_track = next(
            (
                TrackTarget(item.artist, item.title, item.status)
                for item in history
                if item.openable
            ),
            None,
        )

        if active is not None:
            return MenuSnapshot(
                title=f"Running {active.processed_count}/{active.total_urls}",
                detail=_active_detail(active),
                active_job_id=active.job_id,
                active_phase=str(getattr(active, "phase", "") or ""),
                queue_length=len(queued),
                history=history,
                status_lines=[
                    _phase_line(active),
                    _progress_line(active),
                    _queue_line(active, queued_count=len(queued)),
                ],
                primary_track=primary_track,
            )

        if queued:
            return MenuSnapshot(
                title=f"{len(queued)} queued",
                detail="Waiting for daemon worker",
                active_job_id=None,
                active_phase="queued",
                queue_length=len(queued),
                history=history,
                status_lines=[_queued_line(len(queued))],
                primary_track=primary_track,
            )

        with self._lock:
            detail = self._last_feedback
        return MenuSnapshot(
            title="Idle",
            detail=detail,
            active_job_id=None,
            active_phase=None,
            queue_length=0,
            history=history,
            primary_track=primary_track,
        )

    def sync_from_inputs(self) -> SubmissionOutcome:
        """Resolve clipboard/Chrome input and submit the default job request.

        Returns:
            Submission result with a user-facing status message.
        """
        clipboard_text = self.read_clipboard_text()
        bundle_id = self.frontmost_bundle_id()
        log_diagnostic(
            "sync_requested",
            clipboard_has_text=bool(clipboard_text.strip()),
            frontmost_bundle_id=bundle_id,
        )

        clipboard_inspection = self.client.inspect_input(text=clipboard_text)
        log_diagnostic("inspection_result", source="clipboard", inspection=clipboard_inspection)
        clipboard_outcome = self._submit_from_inspection(
            clipboard_inspection,
            source_label="clipboard",
        )
        if clipboard_outcome is not None:
            return clipboard_outcome

        fallback_message = self._feedback_for_inspection(
            clipboard_inspection,
            default="No media URL found",
        )
        if bundle_id != GOOGLE_CHROME_BUNDLE_ID:
            return self._record_feedback(fallback_message)

        try:
            chrome_url = self.read_chrome_url()
        except Exception as exc:
            return self._record_feedback(f"Chrome tab unavailable: {exc}")

        if not chrome_url:
            return self._record_feedback(fallback_message)

        chrome_inspection = self.client.inspect_input(candidates=[chrome_url])
        log_diagnostic(
            "inspection_result",
            source="chrome",
            chrome_url=chrome_url,
            inspection=chrome_inspection,
        )
        chrome_outcome = self._submit_from_inspection(
            chrome_inspection,
            source_label="chrome",
        )
        if chrome_outcome is not None:
            return chrome_outcome
        return self._record_feedback(
            self._feedback_for_inspection(chrome_inspection, default=fallback_message)
        )

    def load_settings(self) -> dict[str, Any]:
        """Fetch current daemon-backed config for the settings window."""
        return self.client.get_config()

    def save_settings(
        self,
        *,
        use_music_app: bool,
        fuzzy_threshold: int,
        staging_dir: str,
        import_destination: str,
    ) -> dict[str, Any]:
        """Persist the v1 settings subset through the daemon.

        Args:
            use_music_app: Whether Music.app integration stays enabled.
            fuzzy_threshold: Fuzzy-match threshold.
            staging_dir: Download staging path text.
            import_destination: Import destination path text.

        Returns:
            Saved config payload from the daemon.
        """
        payload = self.client.update_config(
            {
                "use_music_app": use_music_app,
                "fuzzy_threshold": fuzzy_threshold,
                "staging_dir": staging_dir,
                "import_destination": import_destination,
            }
        )
        log_diagnostic("settings_saved", payload=payload)
        self.set_feedback("Settings saved")
        return payload

    def set_feedback(self, message: str) -> None:
        """Record lightweight UI feedback from menu-side actions.

        Args:
            message: User-facing menu feedback.
        """
        with self._lock:
            self._set_feedback_locked(message)

    def drain_notifications(self) -> list[CompletionNotice]:
        """Return and clear any pending completion notifications."""
        with self._lock:
            notices = list(self._pending_notifications)
            self._pending_notifications.clear()
            return notices

    def _submit_from_inspection(
        self,
        inspection: dict[str, Any],
        *,
        source_label: str,
    ) -> SubmissionOutcome | None:
        """Submit actionable URLs from one inspection payload."""
        urls = [item["normalized_url"] for item in inspection.get("actionable_urls", [])]
        if not urls:
            return None

        submission = self.client.submit_job(JobRequest(urls=urls))
        snapshot = submission.snapshot
        if submission.deduplicated:
            if snapshot.state == "running":
                message = f"Already running from {source_label}"
            else:
                message = f"Already queued from {source_label} (position {snapshot.position})"
        else:
            verb = "Started" if snapshot.state == "running" else "Queued"
            message = f"{verb} {len(urls)} URL(s) from {source_label}"
        with self._lock:
            self._set_feedback_locked(message)
        log_diagnostic(
            "job_submitted",
            job_id=snapshot.job_id,
            source=source_label,
            urls=urls,
            deduplicated=submission.deduplicated,
            state=snapshot.state,
        )
        self._start_watch(snapshot.job_id)
        return SubmissionOutcome(
            submitted=True,
            message=message,
            job_id=snapshot.job_id,
            urls=urls,
        )

    def _feedback_for_inspection(self, inspection: dict[str, Any], *, default: str) -> str:
        """Return a small user-facing message for a rejected inspection."""
        unsupported = inspection.get("recognized_unsupported", [])
        if unsupported:
            source = unsupported[0].get("source", "source")
            return f"Unsupported source for now: {source}"

        invalid = inspection.get("invalid", [])
        if invalid:
            return "No supported media URL found"

        return default

    def _record_feedback(self, message: str) -> SubmissionOutcome:
        """Persist a non-submission message and return it."""
        with self._lock:
            self._set_feedback_locked(message)
        return SubmissionOutcome(submitted=False, message=message)

    def _start_watch(self, job_id: str) -> None:
        """Start a background watcher for one queued job."""
        with self._lock:
            if job_id in self._watching_jobs:
                return
            self._watching_jobs.add(job_id)
        log_diagnostic("job_watch_started", job_id=job_id)

        thread = threading.Thread(
            target=self._watch_job_events,
            args=(job_id,),
            name=f"que-menubar-watch-{job_id}",
            daemon=True,
        )
        thread.start()

    def _watch_job_events(self, job_id: str) -> None:
        """Watch daemon events for one job and keep the menu feedback honest."""
        saw_terminal_summary = False
        reports: list[dict[str, Any]] = []
        try:
            for event in self.client.stream_events(job_id):
                if event.type == "item_report":
                    report = event.payload.get("report")
                    if isinstance(report, dict):
                        reports = [dict(report)]
                elif event.type == "results":
                    payload_reports = event.payload.get("reports")
                    if isinstance(payload_reports, list):
                        reports = [
                            dict(report)
                            for report in payload_reports
                            if isinstance(report, dict)
                        ]

                log_diagnostic(
                    "job_event",
                    job_id=job_id,
                    event_type=event.type,
                    message=_strip_rich_markup(event.message or ""),
                    payload=event.payload,
                )

                if event.type == "summary":
                    with self._lock:
                        message = _summary_feedback(event.payload, reports=reports)
                        self._set_feedback_locked(message)
                        success = int(event.payload.get("stats", {}).get("failed", 0) or 0) == 0
                        track = latest_open_track_from_reports(reports)
                        self._pending_notifications.append(
                            CompletionNotice(
                                title=(track.label() if track and success else "Que Sync Finished"),
                                message=message,
                                success=success,
                                track=track,
                            )
                        )
                    saw_terminal_summary = True
                elif event.type == "job_failed":
                    with self._lock:
                        message = _strip_rich_markup(event.message or f"Job {job_id} failed")
                        self._set_feedback_locked(message)
                        self._pending_notifications.append(
                            CompletionNotice(
                                title="Que Sync Failed",
                                message=message,
                                success=False,
                            )
                        )
                    saw_terminal_summary = True
        except Exception as exc:
            log_diagnostic("job_watch_error", job_id=job_id, error=str(exc))
        finally:
            if not saw_terminal_summary:
                self._poll_terminal_snapshot(job_id)
            with self._lock:
                self._watching_jobs.discard(job_id)

    def _poll_terminal_snapshot(self, job_id: str) -> None:
        """Poll one snapshot as a fallback when the event stream is unavailable."""
        while True:
            snapshot = self.client.get_job(job_id)
            if snapshot.state in TERMINAL_JOB_STATES:
                with self._lock:
                    message = _snapshot_feedback(snapshot)
                    self._set_feedback_locked(message)
                    self._pending_notifications.append(
                        CompletionNotice(
                            title=(
                                "Que Sync Finished"
                                if int(getattr(snapshot, "failed_count", 0) or 0) == 0
                                else "Que Sync Failed"
                            ),
                            message=message,
                            success=int(getattr(snapshot, "failed_count", 0) or 0) == 0,
                        )
                    )
                log_diagnostic(
                    "job_poll_terminal",
                    job_id=job_id,
                    state=snapshot.state,
                    message=message,
                )
                return
            time.sleep(self.poll_interval_seconds)

    def _set_feedback_locked(self, message: str) -> None:
        """Update feedback text and diagnostics under the controller lock."""
        if self._last_feedback == message:
            return
        self._last_feedback = message
        log_diagnostic("feedback_changed", message=message)


def _history_item_from_row(row: tuple[Any, Any, Any, Any, Any]) -> HistoryItem:
    """Convert one daemon history row into menu display text."""
    _url, artist, title, status, processed_at = row
    status_text = str(status or "")
    artist_text = str(artist or "")
    title_text = str(title or "")
    return HistoryItem(
        status=status_text,
        status_label=history_status_label(status_text),
        row_label=_history_row_label(
            status=status_text,
            artist=artist_text,
            title=title_text,
        ),
        artist=artist_text,
        title=title_text,
        relative_time=_relative_time(str(processed_at)),
        openable=is_openable_status(status_text) and bool(artist_text or title_text),
    )


def _history_row_label(*, status: str, artist: str, title: str) -> str:
    """Return a concise history row label.

    Args:
        status: Cached per-URL status.
        artist: Track artist text, if known.
        title: Track title text, if known.

    Returns:
        User-facing summary text for one history row.
    """
    if artist and title:
        return f"{artist} - {title}"
    if artist:
        return artist
    if title:
        return title
    if status == "failed":
        return "Previous attempt failed before track was identified"
    if status == "skipped":
        return "Dry run skipped before download"
    if status == "pending_import":
        return "Track still needs Music import"
    return "Track details unavailable"


def _relative_time(iso_text: str) -> str:
    """Render a compact relative-time string."""
    try:
        then = datetime.fromisoformat(iso_text)
    except ValueError:
        return iso_text[:10]

    delta = max(0, int((datetime.now() - then).total_seconds()))
    if delta < 60:
        return "just now"
    if delta < 3600:
        minutes = delta // 60
        return f"{minutes}m ago"
    if delta < 86400:
        hours = delta // 3600
        return f"{hours}h ago"
    days = delta // 86400
    return f"{days}d ago"


def _summary_feedback(
    payload: dict[str, Any],
    *,
    reports: list[dict[str, Any]] | None = None,
) -> str:
    """Render a small truthful message from a daemon summary payload."""
    if reports and len(reports) == 1:
        note = str(reports[0].get("note") or "").strip()
        if note:
            return note

    stats = dict(payload.get("stats", {}))
    parts = _summary_parts(
        downloaded=int(stats.get("downloaded", 0) or 0),
        in_library=int(stats.get("in_library", 0) or 0),
        cached=int(stats.get("cached", 0) or 0),
        skipped=int(stats.get("skipped", 0) or 0),
        failed=int(stats.get("failed", 0) or 0),
    )
    if parts:
        return "; ".join(parts)
    return "Run finished"


def _snapshot_feedback(snapshot: Any) -> str:
    """Render a fallback message from one terminal snapshot."""
    parts = _summary_parts(
        downloaded=int(getattr(snapshot, "downloaded_count", 0) or 0),
        in_library=int(getattr(snapshot, "in_library_count", 0) or 0),
        cached=int(getattr(snapshot, "cached_count", 0) or 0),
        skipped=int(getattr(snapshot, "skipped_count", 0) or 0),
        failed=int(getattr(snapshot, "failed_count", 0) or 0),
    )
    if parts:
        return "; ".join(parts)
    return _strip_rich_markup(
        getattr(snapshot, "last_message", None) or f"Job {snapshot.job_id} {snapshot.state}"
    )


def _summary_parts(
    *,
    downloaded: int,
    in_library: int,
    cached: int,
    skipped: int,
    failed: int,
) -> list[str]:
    """Render all non-zero summary counters in menu-friendly order."""
    parts: list[str] = []
    if downloaded:
        parts.append(_count_phrase(downloaded, "downloaded/imported"))
    if in_library:
        parts.append(_count_phrase(in_library, "already in library"))
    if cached:
        parts.append(_count_phrase(cached, "cached/verified"))
    if skipped:
        parts.append(_count_phrase(skipped, "dry-run skipped"))
    if failed:
        parts.append(_count_phrase(failed, "failed"))
    return parts


def _count_phrase(count: int, state: str) -> str:
    """Render one count + state phrase with correct singular/plural."""
    noun = "track" if count == 1 else "tracks"
    return f"{count} {noun} {state}"


def _active_detail(snapshot: Any) -> str:
    """Return the primary visible line for an active job."""
    current_track = getattr(snapshot, "current_track", None)
    if current_track is not None:
        artist = str(getattr(current_track, "artist", "") or "")
        title = str(getattr(current_track, "title", "") or "")
        if artist and title:
            return f"{artist} - {title}"
        if artist or title:
            return artist or title
    return _strip_rich_markup(
        str(getattr(snapshot, "detail", "") or getattr(snapshot, "last_message", "") or "")
        or "Job in progress"
    )


def _counts_line(snapshot: Any) -> str:
    """Render a compact aggregate-count line for active fallback snapshots."""
    total_urls = int(getattr(snapshot, "total_urls", 0) or 0)
    processed_count = int(getattr(snapshot, "processed_count", 0) or 0)
    failed_count = int(getattr(snapshot, "failed_count", 0) or 0)
    queued_downloads = int(getattr(snapshot, "queued_downloads", 0) or 0)
    return (
        f"Done {processed_count}/{total_urls} · "
        f"Failed {failed_count} · Queued {queued_downloads}"
    )


def phase_icon(phase: str | None) -> str:
    """Return the icon for one daemon-owned phase."""
    return _PHASE_ICONS.get(str(phase or "").strip(), "•")


def phase_label(phase: str | None) -> str:
    """Return the human label for one daemon-owned phase."""
    normalized = str(phase or "").strip()
    if not normalized:
        return "Working"
    return _PHASE_LABELS.get(normalized, normalized.replace("_", " ").title())


def status_item_title_for_phase(*, phase: str | None, queued: bool, errored: bool) -> str:
    """Return the compact menu-bar title text.

    Args:
        phase: Active daemon phase when a job is running.
        queued: Whether queued work exists without an active job.
        errored: Whether the app is in daemon-error mode.

    Returns:
        Compact menu-bar title.
    """
    if errored:
        return "que!"
    if phase:
        return f"que{phase_icon(phase)}"
    if queued:
        return "que…"
    return "que"


def _phase_line(snapshot: Any) -> str:
    """Render a status line that explains the current pipeline stage."""
    phase = getattr(snapshot, "phase", None)
    return f"{phase_icon(phase)} {phase_label(phase)}"


def _progress_line(snapshot: Any) -> str:
    """Render a compact progress bar from daemon-owned snapshot fields."""
    fraction = float(getattr(snapshot, "progress_fraction", 0.0) or 0.0)
    fraction = max(0.0, min(1.0, fraction))
    filled = max(0, min(10, int(round(fraction * 10))))
    bar = ("■" * filled) + ("·" * (10 - filled))
    line = f"{bar} {int(fraction * 100):02d}%"
    eta_seconds = getattr(snapshot, "eta_seconds", None)
    if eta_seconds not in (None, 0):
        line += f"  ETA {_format_eta(int(eta_seconds))}"
    return line


def _queue_line(snapshot: Any, *, queued_count: int) -> str:
    """Render a queue/progress summary line for an active job."""
    total_urls = int(getattr(snapshot, "total_urls", 0) or 0)
    processed_count = int(getattr(snapshot, "processed_count", 0) or 0)
    if queued_count > 0:
        return f"Progress · {processed_count}/{total_urls} done · {queued_count} waiting behind"
    return f"Progress · {processed_count}/{total_urls} done"


def _queued_line(queued_count: int) -> str:
    """Render a queue-only summary line."""
    noun = "job" if queued_count == 1 else "jobs"
    return f"Queue · {queued_count} {noun} waiting"


def _format_eta(seconds: int) -> str:
    """Render a compact ETA label from total seconds."""
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:d}:{seconds:02d}"


def _strip_rich_markup(text: str) -> str:
    """Remove the small subset of Rich markup that the daemon emits."""
    cleaned = _RICH_TAG_RE.sub("", text)
    return " ".join(cleaned.split())
