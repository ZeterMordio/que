"""Live activity/progress models for the que menu-bar client."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..service.models import JobSnapshot, ServiceEvent

_STAGE_LABELS = {
    "queued": "Queued",
    "preflight": "Pre-download",
    "checking_library": "Checking library",
    "preparing_download": "Preparing download",
    "downloading": "Downloading",
    "importing": "Apple Music import",
    "finishing": "Finishing",
}
_STAGE_PROGRESS = {
    "queued": 0.04,
    "preflight": 0.12,
    "checking_library": 0.18,
    "preparing_download": 0.28,
    "downloading": 0.68,
    "importing": 0.92,
    "finishing": 0.98,
}
_OPENABLE_STATUSES = {"downloaded", "in_library"}


@dataclass(frozen=True)
class TrackTarget:
    """Track reference that can be shown or opened from the menu."""

    artist: str
    title: str
    status: str = ""

    def label(self) -> str:
        """Return a compact human-readable label."""
        if self.artist and self.title:
            return f"{self.artist} - {self.title}"
        return self.artist or self.title or "Unknown Track"


@dataclass(frozen=True)
class ActivitySnapshot:
    """Renderable live-status model for an active job."""

    title: str
    detail: str
    progress_label: str
    counts_label: str
    current_track: TrackTarget | None = None


@dataclass
class JobActivityTracker:
    """Track live menu-friendly activity for one daemon job."""

    job_id: str
    total_urls: int
    started_monotonic: float = field(default_factory=time.monotonic)
    processed_count: int = 0
    stage_key: str = "queued"
    current_index: int = 0
    current_track: TrackTarget | None = None
    latest_open_track: TrackTarget | None = None
    tracks_by_index: dict[int, TrackTarget] = field(default_factory=dict)

    def apply_event(self, event: ServiceEvent) -> None:
        """Update the tracker from one daemon event.

        Args:
            event: Structured daemon event.
        """
        payload = dict(event.payload or {})
        index = int(payload.get("index", 0) or 0)

        if event.type == "run_started":
            self.total_urls = int(payload.get("total_urls", self.total_urls) or self.total_urls)
            self.stage_key = "preflight"
            return

        if event.type == "expanded_urls_ready":
            self.total_urls = int(payload.get("total_urls", self.total_urls) or self.total_urls)
            return

        if event.type == "item_started":
            self.current_index = index or self.current_index
            self.stage_key = "preflight"
            self.current_track = self.tracks_by_index.get(self.current_index)
            return

        if event.type == "metadata_resolved":
            track = TrackTarget(
                artist=str(payload.get("artist") or ""),
                title=str(payload.get("title") or ""),
            )
            if index:
                self.tracks_by_index[index] = track
                self.current_index = index
            self.current_track = track
            self.stage_key = "checking_library"
            return

        if event.type in {"library_hit", "library_miss"}:
            self.current_index = index or self.current_index
            self.current_track = self.tracks_by_index.get(self.current_index, self.current_track)
            self.stage_key = "checking_library"
            return

        if event.type == "download_queued":
            self.current_index = index or self.current_index
            self.current_track = self.tracks_by_index.get(self.current_index, self.current_track)
            self.stage_key = "preparing_download"
            return

        if event.type == "download_queue_started":
            self.stage_key = "downloading"
            return

        if event.type in {
            "worker_download_finished",
            "worker_download_failed",
            "download_completed",
        }:
            self.current_index = index or self.current_index
            self.current_track = self.tracks_by_index.get(self.current_index, self.current_track)
            self.stage_key = "importing"
            return

        if event.type in {"tag_completed", "import_completed", "import_failed"}:
            self.current_index = index or self.current_index
            self.current_track = self.tracks_by_index.get(self.current_index, self.current_track)
            self.stage_key = "importing"
            return

        if event.type == "item_report":
            report = payload.get("report", {})
            if isinstance(report, dict):
                self.processed_count += 1
                track = TrackTarget(
                    artist=str(report.get("artist") or ""),
                    title=str(report.get("title") or ""),
                    status=str(report.get("status") or ""),
                )
                self.current_track = track
                if track.status in _OPENABLE_STATUSES and (track.artist or track.title):
                    self.latest_open_track = track
            self.stage_key = "finishing"

    def render(self, snapshot: JobSnapshot) -> ActivitySnapshot:
        """Build a menu-renderable activity snapshot.

        Args:
            snapshot: Current daemon job snapshot.

        Returns:
            Live activity rows for the menu.
        """
        total_urls = max(1, int(snapshot.total_urls or self.total_urls or 1))
        processed = max(int(snapshot.processed_count or 0), self.processed_count)
        progress_fraction = _progress_fraction(
            processed=processed,
            total_urls=total_urls,
            stage_key=self.stage_key,
        )
        elapsed_seconds = max(0.0, time.monotonic() - self.started_monotonic)
        eta_label = _eta_label(progress_fraction, elapsed_seconds)
        progress_label = f"{_progress_bar(progress_fraction)} {int(progress_fraction * 100):02d}%"
        if eta_label:
            progress_label += f"  ETA {eta_label}"

        track_label = self.current_track.label() if self.current_track else f"Track {processed + 1}"
        detail = f"{_STAGE_LABELS.get(self.stage_key, 'Working')} · {track_label}"
        counts_label = (
            f"Done {processed}/{total_urls} · "
            f"Failed {int(snapshot.failed_count or 0)} · "
            f"Queued {int(snapshot.queued_downloads or 0)}"
        )
        return ActivitySnapshot(
            title=f"Running {processed}/{total_urls}",
            detail=detail,
            progress_label=progress_label,
            counts_label=counts_label,
            current_track=self.current_track,
        )


def latest_open_track_from_reports(reports: list[dict[str, Any]]) -> TrackTarget | None:
    """Return the last openable track from terminal reports.

    Args:
        reports: Final per-item reports from the daemon.

    Returns:
        The last downloaded/in-library track, if any.
    """
    for report in reversed(reports):
        status = str(report.get("status") or "")
        if status not in _OPENABLE_STATUSES:
            continue
        artist = str(report.get("artist") or "")
        title = str(report.get("title") or "")
        if artist or title:
            return TrackTarget(artist=artist, title=title, status=status)
    return None


def history_status_label(status: str) -> str:
    """Return a friendlier status label for history rows."""
    return {
        "downloaded": "Imported",
        "in_library": "Already in Library",
        "failed": "Failed",
        "pending_import": "Needs Music Import",
        "cached": "Cached",
        "skipped": "Skipped",
        "benchmarked": "Downloaded",
    }.get(status, status.replace("_", " ").title())


def is_openable_status(status: str) -> bool:
    """Return True when a history row can be revealed in Music."""
    return status in _OPENABLE_STATUSES


def _progress_fraction(*, processed: int, total_urls: int, stage_key: str) -> float:
    """Return a rough progress fraction for menu-bar visualization."""
    if total_urls <= 0:
        return 0.0
    if processed >= total_urls:
        return 1.0
    stage_fraction = _STAGE_PROGRESS.get(stage_key, 0.0)
    return min(0.99, max(0.0, (processed + stage_fraction) / total_urls))


def _progress_bar(fraction: float, width: int = 10) -> str:
    """Return a compact unicode progress bar."""
    filled = max(0, min(width, int(round(fraction * width))))
    return ("█" * filled) + ("░" * (width - filled))


def _eta_label(progress_fraction: float, elapsed_seconds: float) -> str:
    """Return a compact ETA label from elapsed time and progress."""
    if progress_fraction < 0.06 or elapsed_seconds < 2.0:
        return ""
    remaining = max(0, int((elapsed_seconds / progress_fraction) - elapsed_seconds))
    minutes, seconds = divmod(remaining, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:d}:{seconds:02d}"
