"""Regression tests for main CLI entry points."""
from __future__ import annotations

import io
from datetime import datetime
from types import SimpleNamespace

import pytest
from rich.console import Console

from que import main
from que.service.models import JobSnapshot, ServiceEvent


def _summary_payload(*, run_mode: str = "normal") -> dict[str, object]:
    """Build a summary event payload."""
    return {
        "run_id": 7,
        "run_mode": run_mode,
        "jobs": 3,
        "stats": {
            "downloaded": 1,
            "benchmarked": 0 if run_mode == "normal" else 1,
            "in_library": 0,
            "cached": 0,
            "skipped": 0,
            "failed": 0,
        },
        "preflight_seconds": 0.5,
        "download_phase_seconds": 1.2,
        "total_seconds": 1.7,
    }


def test_main_submits_job_request_and_streams_events(monkeypatch) -> None:
    """Normal CLI execution should submit a daemon job and tail its events."""
    output = io.StringIO()
    calls: dict[str, object] = {}

    class FakeClient:
        def submit_job(self, request):
            calls["request"] = request
            return JobSnapshot(job_id="job-123", state="queued", position=1, total_urls=1)

        def stream_events(self, job_id):
            calls["job_id"] = job_id
            return iter(
                [
                    ServiceEvent(type="log", job_id=job_id, message="  queued"),
                    ServiceEvent(
                        type="results",
                        job_id=job_id,
                        payload={
                            "reports": [
                                {
                                    "index": 1,
                                    "url": "https://youtube.com/watch?v=abc",
                                    "status": "downloaded",
                                    "artist": "Artist",
                                    "title": "Track",
                                    "note": "Imported",
                                }
                            ]
                        },
                    ),
                    ServiceEvent(
                        type="summary",
                        job_id=job_id,
                        payload=_summary_payload(),
                    ),
                ]
            )

    monkeypatch.setattr(
        main,
        "console",
        Console(file=output, force_terminal=False, color_system=None),
    )
    monkeypatch.setattr(main, "DaemonClient", lambda *args, **kwargs: FakeClient())
    monkeypatch.setattr(main, "parse_urls", lambda value: [value])
    monkeypatch.setattr(main, "normalize_url", lambda value: value)
    monkeypatch.setattr(main, "get_urls_from_clipboard", lambda: [])
    monkeypatch.setattr(
        main.sys,
        "argv",
        [
            "que",
            "--no-cache",
            "--playlist",
            "Road Trip",
            "https://youtube.com/watch?v=abc",
        ],
    )

    main.main()

    request = calls["request"]
    assert request.urls == ["https://youtube.com/watch?v=abc"]
    assert request.playlist_name == "Road Trip"
    assert request.use_cache is False
    assert calls["job_id"] == "job-123"

    rendered = output.getvalue()
    assert "queued" in rendered
    assert "que results" in rendered
    assert "downloaded & imported" in rendered


def test_main_jobs_flag_is_passed_to_job_request(monkeypatch) -> None:
    """The CLI should forward `--jobs` to the daemon request."""
    calls: dict[str, object] = {}

    class FakeClient:
        def submit_job(self, request):
            calls["jobs"] = request.jobs
            return JobSnapshot(job_id="job-123", state="queued", position=1, total_urls=1)

        def stream_events(self, job_id):
            return iter([ServiceEvent(type="summary", job_id=job_id, payload=_summary_payload())])

    monkeypatch.setattr(main, "DaemonClient", lambda *args, **kwargs: FakeClient())
    monkeypatch.setattr(main, "parse_urls", lambda value: [value])
    monkeypatch.setattr(main, "normalize_url", lambda value: value)
    monkeypatch.setattr(main, "get_urls_from_clipboard", lambda: [])
    monkeypatch.setattr(
        main.sys,
        "argv",
        ["que", "--jobs", "5", "--no-cache", "https://youtube.com/watch?v=abc"],
    )

    main.main()

    assert calls["jobs"] == 5


def test_main_rejects_invalid_jobs_value(monkeypatch) -> None:
    """`--jobs` must reject values below 1."""
    monkeypatch.setattr(
        main.sys,
        "argv",
        ["que", "--jobs", "0", "https://youtube.com/watch?v=abc"],
    )

    with pytest.raises(SystemExit) as exc_info:
        main.main()

    assert exc_info.value.code == 2


def test_main_help_mentions_download_engine_benchmark(monkeypatch, capsys) -> None:
    """Help text should describe benchmark mode as download-engine-only."""
    monkeypatch.setattr(main.sys, "argv", ["que", "--help"])

    with pytest.raises(SystemExit) as exc_info:
        main.main()

    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "Benchmark download-engine throughput" in help_text
    assert "Benchmark the download engine only" in help_text
    assert "que status" in help_text


def test_main_benchmark_mode_passes_flag_and_disables_cache(monkeypatch) -> None:
    """Benchmark mode should still submit the request through the daemon."""
    calls: dict[str, object] = {}

    class FakeClient:
        def submit_job(self, request):
            calls["benchmark"] = request.benchmark
            calls["use_cache"] = request.use_cache
            return JobSnapshot(job_id="job-123", state="queued", position=1, total_urls=1)

        def stream_events(self, job_id):
            return iter(
                [
                    ServiceEvent(
                        type="summary",
                        job_id=job_id,
                        payload=_summary_payload(run_mode="benchmark"),
                    )
                ]
            )

    monkeypatch.setattr(main, "DaemonClient", lambda *args, **kwargs: FakeClient())
    monkeypatch.setattr(main, "parse_urls", lambda value: [value])
    monkeypatch.setattr(main, "normalize_url", lambda value: value)
    monkeypatch.setattr(main, "get_urls_from_clipboard", lambda: [])
    monkeypatch.setattr(
        main.sys,
        "argv",
        [
            "que",
            "--benchmark",
            "--no-cache",
            "https://youtube.com/watch?v=abc",
        ],
    )

    main.main()

    assert calls["benchmark"] is True
    assert calls["use_cache"] is False


def test_main_rejects_benchmark_playlist_combination(monkeypatch) -> None:
    """Benchmark mode should reject incompatible playlist imports."""
    monkeypatch.setattr(
        main.sys,
        "argv",
        [
            "que",
            "--benchmark",
            "--playlist",
            "Road Trip",
            "https://youtube.com/watch?v=abc",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main.main()

    assert exc_info.value.code == 2


def test_main_runs_command_renders_recent_metrics(monkeypatch) -> None:
    """`que runs` should print recent aggregate performance metrics."""
    output = io.StringIO()
    rows = [
        {
            "run_id": 7,
            "started_at": datetime(2026, 3, 12, 14, 5).isoformat(),
            "finished_at": None,
            "run_mode": "benchmark",
            "jobs": 3,
            "total_urls": 42,
            "playlist_name": None,
            "dry_run": False,
            "preflight_seconds": 1.0,
            "download_phase_seconds": 2.0,
            "total_seconds": 88.2,
            "queued_downloads": 30,
            "downloaded_count": 30,
            "in_library_count": 0,
            "cached_count": 0,
            "skipped_count": 0,
            "failed_count": 2,
            "download_failed_count": 1,
            "import_failed_count": 1,
            "downloaded_bytes": 30 * 1024 * 1024,
            "average_download_bytes_per_second": 1.5 * 1024 * 1024,
        }
    ]

    class FakeClientWrapper:
        def recent_runs(self, limit=20):
            assert limit == 5
            return [
                SimpleNamespace(
                    **(row | {"started_at": datetime.fromisoformat(row["started_at"])})
                )
                for row in rows
            ]

    monkeypatch.setattr(
        main,
        "console",
        Console(file=output, force_terminal=False, color_system=None),
    )
    monkeypatch.setattr(main, "DaemonClient", lambda *args, **kwargs: FakeClientWrapper())
    monkeypatch.setattr(main.sys, "argv", ["que", "runs", "--limit", "5"])

    main.main()

    rendered = output.getvalue()
    assert "que runs" in rendered
    assert "benchmark" in rendered
    assert "30.0 MiB" in rendered
    assert "1.5 MiB/s" in rendered
    assert "2 (d:1/i:1)" in rendered


def test_main_status_command_renders_active_and_queued_jobs(monkeypatch) -> None:
    """`que status` should show active and queued daemon jobs."""
    output = io.StringIO()

    class FakeClient:
        def get_queue(self):
            return {
                "active": JobSnapshot(
                    job_id="job-a",
                    state="running",
                    position=0,
                    total_urls=3,
                    processed_count=1,
                    failed_count=0,
                ),
                "queued": [
                    JobSnapshot(
                        job_id="job-b",
                        state="queued",
                        position=1,
                        total_urls=2,
                    )
                ],
            }

    monkeypatch.setattr(
        main,
        "console",
        Console(file=output, force_terminal=False, color_system=None),
    )
    monkeypatch.setattr(main, "DaemonClient", lambda *args, **kwargs: FakeClient())
    monkeypatch.setattr(main.sys, "argv", ["que", "status"])

    main.main()

    rendered = output.getvalue()
    assert "que status" in rendered
    assert "job-a" in rendered
    assert "job-b" in rendered
