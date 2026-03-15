"""Core service-layer request, snapshot, and event models for que."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

TERMINAL_JOB_STATES = {"completed", "failed"}


def _now_iso() -> str:
    """Return the current local timestamp as ISO text."""
    return datetime.now().isoformat()


@dataclass
class JobRequest:
    """Serializable request payload for a daemon-backed job."""

    urls: list[str]
    dry_run: bool = False
    benchmark: bool = False
    playlist_name: str | None = None
    jobs: int = 3
    threshold_override: int | None = None
    use_cache: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dictionary form."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobRequest:
        """Build a request model from untrusted JSON data."""
        urls = data.get("urls")
        if not isinstance(urls, list) or not urls:
            raise ValueError("urls must be a non-empty list")
        normalized_urls = [str(url).strip() for url in urls if str(url).strip()]
        if not normalized_urls:
            raise ValueError("urls must contain at least one non-empty value")

        jobs = int(data.get("jobs", 3))
        if jobs < 1:
            raise ValueError("jobs must be >= 1")

        threshold_override = data.get("threshold_override")
        if threshold_override is not None:
            threshold_override = int(threshold_override)
            if not 0 <= threshold_override <= 100:
                raise ValueError("threshold_override must be between 0 and 100")

        request = cls(
            urls=normalized_urls,
            dry_run=bool(data.get("dry_run", False)),
            benchmark=bool(data.get("benchmark", False)),
            playlist_name=(
                str(data["playlist_name"])
                if data.get("playlist_name") not in (None, "")
                else None
            ),
            jobs=jobs,
            threshold_override=threshold_override,
            use_cache=bool(data.get("use_cache", True)),
        )
        request.validate()
        return request

    def validate(self) -> None:
        """Validate combinations that must match the CLI behavior."""
        if self.benchmark and self.dry_run:
            raise ValueError("benchmark cannot be combined with dry_run")
        if self.benchmark and self.playlist_name:
            raise ValueError("benchmark cannot be combined with playlist_name")


@dataclass
class JobSnapshot:
    """Current view of one daemon-managed job."""

    job_id: str
    state: str
    position: int
    total_urls: int
    processed_count: int = 0
    downloaded_count: int = 0
    benchmarked_count: int = 0
    in_library_count: int = 0
    cached_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    queued_downloads: int = 0
    run_id: int | None = None
    run_mode: str = "normal"
    created_at: str = field(default_factory=_now_iso)
    started_at: str | None = None
    finished_at: str | None = None
    last_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dictionary form."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobSnapshot:
        """Rehydrate a snapshot from JSON."""
        return cls(
            job_id=str(data["job_id"]),
            state=str(data["state"]),
            position=int(data.get("position", 0)),
            total_urls=int(data.get("total_urls", 0)),
            processed_count=int(data.get("processed_count", 0)),
            downloaded_count=int(data.get("downloaded_count", 0)),
            benchmarked_count=int(data.get("benchmarked_count", 0)),
            in_library_count=int(data.get("in_library_count", 0)),
            cached_count=int(data.get("cached_count", 0)),
            skipped_count=int(data.get("skipped_count", 0)),
            failed_count=int(data.get("failed_count", 0)),
            queued_downloads=int(data.get("queued_downloads", 0)),
            run_id=data.get("run_id"),
            run_mode=str(data.get("run_mode", "normal")),
            created_at=str(data.get("created_at", _now_iso())),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            last_message=data.get("last_message"),
        )


@dataclass
class ServiceEvent:
    """Structured event emitted by the daemon for one job."""

    type: str
    job_id: str
    timestamp: str = field(default_factory=_now_iso)
    message: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dictionary form."""
        return {
            "type": self.type,
            "job_id": self.job_id,
            "timestamp": self.timestamp,
            "message": self.message,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ServiceEvent:
        """Rehydrate an event from JSON."""
        return cls(
            type=str(data["type"]),
            job_id=str(data["job_id"]),
            timestamp=str(data.get("timestamp", _now_iso())),
            message=data.get("message"),
            payload=dict(data.get("payload", {})),
        )
