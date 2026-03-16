"""Regression tests for the local daemon service layer."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import que.config as config
import que.service.executor as service_executor
import que.service.state as service_state
from que.pipeline import ProcessResult
from que.service.client import DaemonClient
from que.service.models import JobRequest
from que.service.server import serve_forever
from que.service.state import DaemonState


def _noop_cache():
    """Return a tiny cache stub with only a close method."""
    return type("Cache", (), {"close": lambda self: None})()


def _fake_result(url: str, run_id: int = 1) -> ProcessResult:
    """Build a small completed ProcessResult."""
    return ProcessResult(
        run_id=run_id,
        run_mode="normal",
        jobs=3,
        total_urls=1,
        reports={},
        stats={
            "downloaded": 1,
            "benchmarked": 0,
            "in_library": 0,
            "cached": 0,
            "skipped": 0,
            "failed": 0,
        },
        preflight_seconds=0.1,
        download_phase_seconds=0.2,
        total_seconds=0.3,
        queued_downloads=1,
    )


def _emit_completed_events(event_handler, url: str, run_id: int = 1) -> None:
    """Emit the minimum event set for one completed daemon job."""
    event_handler("expanded_urls_ready", None, {"total_urls": 1})
    event_handler(
        "item_report",
        None,
        {
            "report": {
                "index": 1,
                "url": url,
                "status": "downloaded",
                "artist": "Artist",
                "title": "Track",
                "note": "Imported",
            }
        },
    )
    event_handler(
        "results",
        None,
        {
            "reports": [
                {
                    "index": 1,
                    "url": url,
                    "status": "downloaded",
                    "artist": "Artist",
                    "title": "Track",
                    "note": "Imported",
                }
            ]
        },
    )
    event_handler(
        "summary",
        None,
        {
            "run_id": run_id,
            "run_mode": "normal",
            "jobs": 3,
            "stats": {
                "downloaded": 1,
                "benchmarked": 0,
                "in_library": 0,
                "cached": 0,
                "skipped": 0,
                "failed": 0,
            },
            "preflight_seconds": 0.1,
            "download_phase_seconds": 0.2,
            "total_seconds": 0.3,
        },
    )


def test_daemon_state_queues_second_job_until_first_completes(monkeypatch) -> None:
    """Only one daemon job should run at a time; later jobs stay queued."""
    first_started = threading.Event()
    release_first = threading.Event()
    execution_order: list[str] = []

    def fake_execute_job(request, event_handler=None):
        url = request.urls[0]
        execution_order.append(url)
        if url == "u1":
            first_started.set()
            assert release_first.wait(timeout=2)
        _emit_completed_events(event_handler, url, run_id=len(execution_order))
        return _fake_result(url, run_id=len(execution_order))

    monkeypatch.setattr(service_state, "execute_job", fake_execute_job)
    state = DaemonState()

    first = state.submit_job(JobRequest(urls=["u1"])).snapshot
    second = state.submit_job(JobRequest(urls=["u2"])).snapshot

    assert first_started.wait(timeout=2)
    assert state.get_job(first.job_id).state == "running"

    queued = state.get_job(second.job_id)
    assert queued is not None
    assert queued.state == "queued"
    assert queued.position == 1

    release_first.set()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        snapshot = state.get_job(second.job_id)
        if snapshot is not None and snapshot.state == "completed":
            break
        time.sleep(0.05)

    assert state.get_job(first.job_id).state == "completed"
    assert state.get_job(second.job_id).state == "completed"
    assert execution_order == ["u1", "u2"]


def test_daemon_state_deduplicates_identical_active_or_queued_jobs(monkeypatch) -> None:
    """Exact duplicate submissions should attach to the existing active job."""
    first_started = threading.Event()
    release_first = threading.Event()
    execution_order: list[str] = []

    def fake_execute_job(request, event_handler=None):
        execution_order.append(request.urls[0])
        first_started.set()
        assert release_first.wait(timeout=2)
        _emit_completed_events(event_handler, request.urls[0], run_id=1)
        return _fake_result(request.urls[0], run_id=1)

    monkeypatch.setattr(service_state, "execute_job", fake_execute_job)
    state = DaemonState()

    first = state.submit_job(JobRequest(urls=["https://youtube.com/watch?v=abc"]))
    assert first_started.wait(timeout=2)
    second = state.submit_job(JobRequest(urls=["https://youtube.com/watch?v=abc"]))

    assert second.deduplicated is True
    assert second.snapshot.job_id == first.snapshot.job_id
    assert execution_order == ["https://youtube.com/watch?v=abc"]

    release_first.set()


def test_daemon_client_autostart_spawns_foreground_daemon(monkeypatch, tmp_path) -> None:
    """Client autostart should spawn `python -m que.main daemon --foreground`."""
    attempts = {"count": 0}
    popen_calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_request_json(self, method, path, **kwargs):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RuntimeError("daemon down")
        return {"status": "ok"}

    def fake_popen(cmd, **kwargs):
        popen_calls.append((cmd, kwargs))
        return type("FakeProc", (), {"pid": 12345})()

    monkeypatch.setattr(DaemonClient, "_request_json", fake_request_json)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(time, "sleep", lambda *_args, **_kwargs: None)

    client = DaemonClient(socket_path=tmp_path / "daemon.sock", startup_timeout_seconds=1)
    client._ensure_running()

    assert popen_calls
    assert popen_calls[0][0][:4] == [sys.executable, "-m", "que.main", "daemon"]
    assert "--foreground" in popen_calls[0][0]


def test_server_client_streams_events_over_unix_socket(monkeypatch) -> None:
    """Server and client should exchange JSON and SSE over a Unix socket."""
    suffix = f"{os.getpid()}-{time.time_ns() % 100000}"
    socket_path = Path("/tmp") / f"que-{suffix}.sock"
    pid_path = Path("/tmp") / f"que-{suffix}.pid"
    socket_path.write_text("stale", encoding="utf-8")

    def fake_execute_job(request, event_handler=None):
        _emit_completed_events(event_handler, request.urls[0], run_id=11)
        return _fake_result(request.urls[0], run_id=11)

    monkeypatch.setattr(service_state, "execute_job", fake_execute_job)

    server_thread = threading.Thread(
        target=serve_forever,
        args=(socket_path, pid_path),
        daemon=True,
    )
    server_thread.start()

    client = DaemonClient(
        socket_path=socket_path,
        autostart=False,
        startup_timeout_seconds=2,
    )

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            client.health()
            break
        except RuntimeError:
            time.sleep(0.05)
    else:
        raise AssertionError("daemon server did not start in time")

    submission = client.submit_job(JobRequest(urls=["u1"]))
    events = list(client.stream_events(submission.snapshot.job_id))
    final_snapshot = client.get_job(submission.snapshot.job_id)

    event_types = [event.type for event in events]
    assert "job_queued" in event_types
    assert "job_started" in event_types
    assert "results" in event_types
    assert "summary" in event_types
    assert "job_finished" in event_types
    assert final_snapshot.state == "completed"
    assert final_snapshot.phase == "completed"
    assert final_snapshot.progress_fraction == 1.0

    client.stop()
    server_thread.join(timeout=2)
    assert not server_thread.is_alive()


def test_executor_does_not_expand_watch_urls_with_playlist_context(monkeypatch) -> None:
    """Watch URLs should stay single-track jobs even when YouTube adds list context."""
    monkeypatch.setattr(
        service_executor,
        "load_config",
        lambda: type(
            "Config",
            (),
            {
                "fuzzy_threshold": 85,
                "cache_path": Path("/tmp/que-test-cache.db"),
                "use_music_app": True,
            },
        )(),
    )
    monkeypatch.setattr(service_executor, "Cache", lambda _path: _noop_cache())
    monkeypatch.setattr(service_executor, "_NullCache", lambda: _noop_cache())
    monkeypatch.setattr(
        service_executor,
        "process_urls",
        lambda urls, **_kwargs: urls,
    )
    monkeypatch.setattr(
        service_executor,
        "expand_playlist",
        lambda _url: (_ for _ in ()).throw(AssertionError("watch URL should not expand")),
    )

    result = service_executor.execute_job(
        JobRequest(urls=["https://www.youtube.com/watch?v=abc&list=RD123&index=28"]),
    )

    assert result == ["https://www.youtube.com/watch?v=abc"]


def test_executor_expands_explicit_playlist_urls(monkeypatch) -> None:
    """Pure playlist URLs should still expand before entering the pipeline."""
    monkeypatch.setattr(
        service_executor,
        "load_config",
        lambda: type(
            "Config",
            (),
            {
                "fuzzy_threshold": 85,
                "cache_path": Path("/tmp/que-test-cache.db"),
                "use_music_app": True,
            },
        )(),
    )
    monkeypatch.setattr(service_executor, "Cache", lambda _path: _noop_cache())
    monkeypatch.setattr(
        service_executor,
        "expand_playlist",
        lambda _url: ["https://www.youtube.com/watch?v=a", "https://www.youtube.com/watch?v=b"],
    )
    monkeypatch.setattr(
        service_executor,
        "process_urls",
        lambda urls, **_kwargs: urls,
    )

    result = service_executor.execute_job(
        JobRequest(urls=["https://www.youtube.com/playlist?list=PL123&si=abc"]),
    )

    assert result == [
        "https://www.youtube.com/watch?v=a",
        "https://www.youtube.com/watch?v=b",
    ]


def test_server_client_supports_config_and_intake_endpoints(monkeypatch, tmp_path) -> None:
    """Config and intake endpoints should round-trip through the daemon client."""
    suffix = f"{os.getpid()}-{time.time_ns() % 100000}"
    socket_path = Path("/tmp") / f"que-{suffix}.sock"
    pid_path = Path("/tmp") / f"que-{suffix}.pid"
    config_path = tmp_path / "config" / "config.toml"

    monkeypatch.setattr(config, "CONFIG_PATH", config_path)

    server_thread = threading.Thread(
        target=serve_forever,
        args=(socket_path, pid_path),
        daemon=True,
    )
    server_thread.start()

    client = DaemonClient(
        socket_path=socket_path,
        autostart=False,
        startup_timeout_seconds=2,
    )

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            client.health()
            break
        except RuntimeError:
            time.sleep(0.05)
    else:
        raise AssertionError("daemon server did not start in time")

    current = client.get_config()
    updated = client.update_config(
        {
            "use_music_app": False,
            "fuzzy_threshold": 72,
            "staging_dir": str(tmp_path / "staging"),
            "import_destination": str(tmp_path / "library"),
        }
    )
    inspection = client.inspect_input(
        text=(
            "https://youtube.com/watch?v=abc&si=123\n"
            "https://open.spotify.com/track/123\n"
            "https://example.com/nope"
        )
    )

    assert current["fuzzy_threshold"] == 85
    assert updated["use_music_app"] is False
    assert updated["fuzzy_threshold"] == 72
    assert updated["staging_dir"] == str(tmp_path / "staging")
    assert config.load_config(config_path).import_destination == tmp_path / "library"
    assert [item["source"] for item in inspection["actionable_urls"]] == ["youtube"]
    assert [item["source"] for item in inspection["recognized_unsupported"]] == ["spotify"]
    assert inspection["invalid"][0]["reason"] == "unrecognized_url"

    client.stop()
    server_thread.join(timeout=2)
    assert not server_thread.is_alive()
