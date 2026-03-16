"""Shared daemon-side progress projection for CLI and menu clients."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .models import JobSnapshot, TrackRef

_RICH_TAG_RE = re.compile(r"\[/?[^\]]+\]")
_STAGE_PROGRESS = {
    "queued": 0.0,
    "preflight": 0.12,
    "checking_library": 0.18,
    "preparing_download": 0.28,
    "downloading": 0.68,
    "importing": 0.92,
    "finishing": 0.98,
    "completed": 1.0,
    "failed": 1.0,
}


def initialize_snapshot_progress(snapshot: JobSnapshot) -> None:
    """Initialize shared presentation fields for a new or restarted snapshot.

    Args:
        snapshot: Mutable daemon job snapshot.
    """
    if snapshot.state == "queued":
        snapshot.phase = "queued"
        snapshot.detail = "Waiting in queue"
        snapshot.progress_fraction = 0.0
        snapshot.eta_seconds = None
        return

    if snapshot.state == "running":
        if snapshot.phase in {"", "queued"}:
            snapshot.phase = "preflight"
        snapshot.detail = snapshot.detail or "Starting job"

    _refresh_progress(snapshot)


def project_pipeline_event(
    snapshot: JobSnapshot,
    *,
    event_type: str,
    message: str | None,
    payload: dict[str, Any],
) -> None:
    """Project one pipeline event onto shared snapshot presentation fields.

    Args:
        snapshot: Mutable daemon job snapshot.
        event_type: Structured daemon event type.
        message: Optional rendered message emitted by the pipeline.
        payload: Structured daemon event payload.
    """
    plain_message = _strip_rich_markup(message or "")
    index = int(payload.get("index", 0) or 0)

    if event_type == "queue_position_updated" and snapshot.state == "queued":
        snapshot.phase = "queued"
        snapshot.detail = "Waiting in queue"
        snapshot.progress_fraction = 0.0
        snapshot.eta_seconds = None
        return

    if event_type == "job_started":
        snapshot.phase = "preflight"
        snapshot.detail = "Starting job"
        snapshot.progress_fraction = max(snapshot.progress_fraction, 0.04)
        snapshot.eta_seconds = None
        return

    if event_type == "expanded_urls_ready":
        snapshot.total_urls = int(
            payload.get("total_urls", snapshot.total_urls) or snapshot.total_urls
        )
        snapshot.phase = "preflight"
        snapshot.detail = plain_message or f"Found {snapshot.total_urls} URL(s)"
        _refresh_progress(snapshot)
        return

    if event_type == "playlist_expanded":
        snapshot.phase = "preflight"
        snapshot.detail = plain_message or snapshot.detail
        _refresh_progress(snapshot)
        return

    if event_type == "item_started":
        snapshot.phase = "preflight"
        total = max(1, int(payload.get("total", snapshot.total_urls) or snapshot.total_urls or 1))
        snapshot.detail = f"Track {index or max(1, snapshot.processed_count + 1)}/{total}"
        snapshot.current_track = None
        _refresh_progress(snapshot)
        return

    if event_type == "metadata_resolved":
        snapshot.phase = "checking_library"
        snapshot.current_track = TrackRef(
            artist=str(payload.get("artist") or ""),
            title=str(payload.get("title") or ""),
        )
        snapshot.detail = snapshot.current_track.label()
        _refresh_progress(snapshot)
        return

    if event_type in {"library_hit", "library_miss", "library_bypass_cached_miss"}:
        snapshot.phase = "checking_library"
        if snapshot.current_track is not None:
            snapshot.detail = snapshot.current_track.label()
        elif plain_message:
            snapshot.detail = plain_message
        _refresh_progress(snapshot)
        return

    if event_type == "download_queued":
        snapshot.phase = "preparing_download"
        if snapshot.current_track is not None:
            snapshot.detail = snapshot.current_track.label()
        elif plain_message:
            snapshot.detail = plain_message
        _refresh_progress(snapshot)
        return

    if event_type in {"download_queue_started", "download_queue_staggered"}:
        snapshot.phase = "downloading"
        snapshot.detail = plain_message or "Downloading queued tracks"
        _refresh_progress(snapshot)
        return

    if event_type in {"worker_download_finished", "worker_download_failed", "download_completed"}:
        snapshot.phase = "downloading"
        if snapshot.current_track is not None:
            snapshot.detail = snapshot.current_track.label()
        elif plain_message:
            snapshot.detail = plain_message
        _refresh_progress(snapshot)
        return

    if event_type == "tag_completed":
        snapshot.phase = "importing"
        if snapshot.current_track is not None:
            snapshot.detail = snapshot.current_track.label()
        _refresh_progress(snapshot)
        return

    if event_type in {"import_completed", "import_failed"}:
        snapshot.phase = "importing"
        if snapshot.current_track is not None:
            snapshot.detail = snapshot.current_track.label()
        elif plain_message:
            snapshot.detail = plain_message
        _refresh_progress(snapshot)
        return

    if event_type == "item_report":
        report = payload.get("report", {})
        if isinstance(report, dict):
            snapshot.current_track = TrackRef(
                artist=str(report.get("artist") or ""),
                title=str(report.get("title") or ""),
            )
            snapshot.phase = "finishing"
            note = str(report.get("note") or "").strip()
            snapshot.detail = note or snapshot.current_track.label()
        _refresh_progress(snapshot)
        return

    if event_type == "summary":
        snapshot.phase = "completed"
        snapshot.progress_fraction = 1.0
        snapshot.eta_seconds = 0
        if plain_message:
            snapshot.detail = plain_message
        return

    if event_type == "job_failed":
        snapshot.phase = "failed"
        snapshot.progress_fraction = 1.0
        snapshot.eta_seconds = 0
        snapshot.detail = plain_message or "Job failed"
        return

    if plain_message:
        snapshot.detail = plain_message
        _refresh_progress(snapshot)


def _refresh_progress(snapshot: JobSnapshot) -> None:
    """Recompute progress and ETA from current snapshot counters."""
    total = max(1, int(snapshot.total_urls or 1))
    processed = max(0, int(snapshot.processed_count or 0))
    if snapshot.state == "queued":
        snapshot.progress_fraction = 0.0
        snapshot.eta_seconds = None
        return

    if snapshot.state in {"completed", "failed"}:
        snapshot.progress_fraction = 1.0
        snapshot.eta_seconds = 0
        return

    stage_fraction = _STAGE_PROGRESS.get(snapshot.phase, 0.0)
    snapshot.progress_fraction = min(0.99, max(0.0, (processed + stage_fraction) / total))
    snapshot.eta_seconds = _estimate_eta_seconds(snapshot)


def _estimate_eta_seconds(snapshot: JobSnapshot) -> int | None:
    """Estimate remaining seconds from elapsed runtime and projected progress."""
    if snapshot.progress_fraction < 0.06 or not snapshot.started_at:
        return None

    try:
        elapsed = max(
            0.0,
            (datetime.now() - datetime.fromisoformat(snapshot.started_at)).total_seconds(),
        )
    except ValueError:
        return None

    if elapsed < 2.0 or snapshot.progress_fraction <= 0.0:
        return None
    return max(0, int((elapsed / snapshot.progress_fraction) - elapsed))


def _strip_rich_markup(text: str) -> str:
    """Remove the small subset of Rich markup emitted by the pipeline."""
    return _RICH_TAG_RE.sub("", text).replace("  ", " ").strip()
