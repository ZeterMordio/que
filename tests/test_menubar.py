"""Regression tests for menu-bar controller behavior."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import que.menubar.app as menubar_app
import que.menubar.controller as menubar_controller
import que.menubar.music as menubar_music
from que.menubar.bundle import py2app_options
from que.menubar.controller import (
    GOOGLE_CHROME_BUNDLE_ID,
    MenuBarController,
    _snapshot_feedback,
    _summary_feedback,
    status_item_title_for_phase,
)
from que.menubar.diagnostics import diagnostics_log_path, log_diagnostic
from que.service.models import JobSnapshot, JobSubmission, ServiceEvent, TrackRef


class FakeClient:
    """Small fake daemon client for menu-bar tests."""

    def __init__(self, inspections=None) -> None:
        self.inspections = list(inspections or [])
        self.submitted_requests = []
        self.updated_payloads = []
        self.config_payload = {
            "config_path": "/tmp/config.toml",
            "use_music_app": True,
            "fuzzy_threshold": 85,
            "staging_dir": "/tmp/staging",
            "import_destination": "/tmp/library",
        }

    def inspect_input(self, **_kwargs):
        return self.inspections.pop(0)

    def submit_job(self, request):
        self.submitted_requests.append(request)
        return JobSubmission(
            snapshot=JobSnapshot(job_id="job-123", state="running", position=0, total_urls=1),
            deduplicated=False,
        )

    def get_job(self, _job_id):
        return SimpleNamespace(state="completed", last_message="done")

    def get_queue(self):
        return {"active": None, "queued": []}

    def recent(self, limit=3):
        assert limit == 3
        return []

    def get_config(self):
        return dict(self.config_payload)

    def update_config(self, payload):
        self.updated_payloads.append(payload)
        self.config_payload = self.config_payload | payload
        return dict(self.config_payload)

    def stream_events(self, _job_id):
        return iter([])


@pytest.fixture(autouse=True)
def _quiet_diagnostics(monkeypatch, tmp_path) -> None:
    """Keep diagnostics writes isolated inside the test temp dir."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(menubar_controller, "log_diagnostic", lambda *args, **kwargs: None)
    monkeypatch.setattr(menubar_app, "log_diagnostic", lambda *args, **kwargs: None)


def _inspection(*, actionable=None, unsupported=None, invalid=None):
    """Build a daemon intake payload."""
    return {
        "actionable_urls": list(actionable or []),
        "recognized_unsupported": list(unsupported or []),
        "invalid": list(invalid or []),
    }


def test_controller_submits_clipboard_actionable_urls(monkeypatch) -> None:
    """Clipboard URLs should submit immediately without Chrome fallback."""
    client = FakeClient(
        inspections=[
            _inspection(
                actionable=[
                    {
                        "normalized_url": "https://youtube.com/watch?v=abc",
                        "source": "youtube",
                    }
                ]
            )
        ]
    )
    controller = MenuBarController(
        client,
        read_clipboard_text=lambda: "https://youtube.com/watch?v=abc",
        frontmost_bundle_id=lambda: None,
        read_chrome_url=lambda: None,
    )
    monkeypatch.setattr(controller, "_start_watch", lambda *_args, **_kwargs: None)

    outcome = controller.sync_from_inputs()

    assert outcome.submitted is True
    assert client.submitted_requests[0].urls == ["https://youtube.com/watch?v=abc"]


def test_controller_uses_chrome_when_clipboard_has_no_supported_url(monkeypatch) -> None:
    """Chrome fallback should submit when the clipboard has nothing actionable."""
    client = FakeClient(
        inspections=[
            _inspection(
                invalid=[
                    {
                        "normalized_url": "https://example.com",
                        "reason": "unrecognized_url",
                    }
                ]
            ),
            _inspection(
                actionable=[
                    {
                        "normalized_url": "https://soundcloud.com/artist/track",
                        "source": "soundcloud",
                    }
                ]
            ),
        ]
    )
    controller = MenuBarController(
        client,
        read_clipboard_text=lambda: "",
        frontmost_bundle_id=lambda: GOOGLE_CHROME_BUNDLE_ID,
        read_chrome_url=lambda: "https://soundcloud.com/artist/track",
    )
    monkeypatch.setattr(controller, "_start_watch", lambda *_args, **_kwargs: None)

    outcome = controller.sync_from_inputs()

    assert outcome.submitted is True
    assert client.submitted_requests[0].urls == ["https://soundcloud.com/artist/track"]


def test_controller_falls_through_from_unsupported_clipboard_to_chrome(monkeypatch) -> None:
    """Unsupported clipboard URLs should still allow actionable Chrome fallback."""
    client = FakeClient(
        inspections=[
            _inspection(
                unsupported=[
                    {"normalized_url": "https://open.spotify.com/track/123", "source": "spotify"}
                ]
            ),
            _inspection(
                actionable=[
                    {
                        "normalized_url": "https://youtube.com/watch?v=abc",
                        "source": "youtube",
                    }
                ]
            ),
        ]
    )
    controller = MenuBarController(
        client,
        read_clipboard_text=lambda: "https://open.spotify.com/track/123",
        frontmost_bundle_id=lambda: GOOGLE_CHROME_BUNDLE_ID,
        read_chrome_url=lambda: "https://youtube.com/watch?v=abc",
    )
    monkeypatch.setattr(controller, "_start_watch", lambda *_args, **_kwargs: None)

    outcome = controller.sync_from_inputs()

    assert outcome.submitted is True
    assert client.submitted_requests[0].urls == ["https://youtube.com/watch?v=abc"]


def test_controller_reports_unsupported_chrome_url_without_crashing() -> None:
    """Unsupported Chrome URLs should not submit a job."""
    client = FakeClient(
        inspections=[
            _inspection(),
            _inspection(
                unsupported=[
                    {"normalized_url": "https://www.queup.net/room/demo", "source": "queup"}
                ]
            ),
        ]
    )
    controller = MenuBarController(
        client,
        read_clipboard_text=lambda: "",
        frontmost_bundle_id=lambda: GOOGLE_CHROME_BUNDLE_ID,
        read_chrome_url=lambda: "https://www.queup.net/room/demo",
    )

    outcome = controller.sync_from_inputs()

    assert outcome.submitted is False
    assert "Unsupported source for now: queup" == outcome.message
    assert client.submitted_requests == []


def test_controller_handles_chrome_access_failure() -> None:
    """Chrome automation errors should become lightweight feedback."""
    client = FakeClient(inspections=[_inspection()])
    controller = MenuBarController(
        client,
        read_clipboard_text=lambda: "",
        frontmost_bundle_id=lambda: GOOGLE_CHROME_BUNDLE_ID,
        read_chrome_url=lambda: (_ for _ in ()).throw(RuntimeError("not allowed")),
    )

    outcome = controller.sync_from_inputs()

    assert outcome.submitted is False
    assert "Chrome tab unavailable: not allowed" == outcome.message


def test_controller_loads_and_saves_settings() -> None:
    """Settings helpers should proxy through the daemon client."""
    client = FakeClient()
    controller = MenuBarController(
        client,
        read_clipboard_text=lambda: "",
        frontmost_bundle_id=lambda: None,
        read_chrome_url=lambda: None,
    )

    loaded = controller.load_settings()
    saved = controller.save_settings(
        use_music_app=False,
        fuzzy_threshold=72,
        staging_dir="/tmp/next-staging",
        import_destination="/tmp/next-library",
    )

    assert loaded["fuzzy_threshold"] == 85
    assert client.updated_payloads == [
        {
            "use_music_app": False,
            "fuzzy_threshold": 72,
            "staging_dir": "/tmp/next-staging",
            "import_destination": "/tmp/next-library",
        }
    ]
    assert saved["use_music_app"] is False
    assert saved["fuzzy_threshold"] == 72


def test_py2app_bundle_marks_app_as_menu_bar_only() -> None:
    """Bundle metadata should opt into LSUIElement mode."""
    options = py2app_options()

    assert options["plist"]["LSUIElement"] is True
    assert options["plist"]["CFBundleDisplayName"] == "Que Menu"
    assert options["packages"] == ["que"]
    assert "que.menubar.app" in options["includes"]


def test_summary_feedback_reports_real_download_outcome() -> None:
    """Menu feedback should distinguish download success from mere completion."""
    feedback = _summary_feedback(
        {
            "stats": {
                "downloaded": 1,
                "in_library": 0,
                "cached": 0,
                "skipped": 0,
                "failed": 0,
            }
        }
    )

    assert feedback == "1 track downloaded/imported"


def test_summary_feedback_includes_failures_when_run_is_mixed() -> None:
    """Mixed runs should not hide failed items behind a success count."""
    feedback = _summary_feedback(
        {
            "stats": {
                "downloaded": 1,
                "in_library": 0,
                "cached": 0,
                "skipped": 0,
                "failed": 1,
            }
        }
    )

    assert feedback == "1 track downloaded/imported; 1 track failed"


def test_summary_feedback_reports_failures_without_claiming_success() -> None:
    """A completed run with failures should not look like a successful import."""
    feedback = _summary_feedback(
        {
            "stats": {
                "downloaded": 0,
                "in_library": 0,
                "cached": 0,
                "skipped": 0,
                "failed": 1,
            }
        }
    )

    assert feedback == "1 track failed"


def test_watch_job_events_uses_summary_event_for_feedback() -> None:
    """Event-stream summaries should drive the final menu-bar message."""
    client = FakeClient()
    client.stream_events = lambda _job_id: iter(
        [
            ServiceEvent(
                type="summary",
                job_id="job-123",
                payload={
                    "stats": {
                        "downloaded": 0,
                        "in_library": 1,
                        "cached": 0,
                        "skipped": 0,
                        "failed": 0,
                    }
                },
            )
        ]
    )
    controller = MenuBarController(
        client,
        read_clipboard_text=lambda: "",
        frontmost_bundle_id=lambda: None,
        read_chrome_url=lambda: None,
    )

    controller._watch_job_events("job-123")

    assert controller.refresh_snapshot().detail == "1 track already in library"


def test_refresh_snapshot_exposes_live_progress_rows() -> None:
    """Active jobs should render phase, progress, and queue rows from daemon state."""
    client = FakeClient()
    client.get_queue = lambda: {
        "active": JobSnapshot(
            job_id="job-123",
            state="running",
            position=0,
            processed_count=0,
            total_urls=1,
            last_message="Job in progress",
            phase="downloading",
            detail="Andromedik - Forever",
            current_track=TrackRef(artist="Andromedik", title="Forever"),
            failed_count=0,
            queued_downloads=1,
            progress_fraction=0.68,
            eta_seconds=42,
        ),
        "queued": [],
    }
    controller = MenuBarController(
        client,
        read_clipboard_text=lambda: "",
        frontmost_bundle_id=lambda: None,
        read_chrome_url=lambda: None,
    )

    snapshot = controller.refresh_snapshot()

    assert snapshot.title == "Running 0/1"
    assert snapshot.detail == "Andromedik - Forever"
    assert snapshot.active_phase == "downloading"
    assert len(snapshot.status_lines) == 3
    assert snapshot.status_lines[0] == "↓ Downloading"
    assert "%" in snapshot.status_lines[1]
    assert "ETA 0:42" in snapshot.status_lines[1]
    assert snapshot.status_lines[2] == "Progress · 0/1 done"
    assert snapshot.primary_track is None


def test_refresh_snapshot_keeps_last_finished_track_visible_during_active_run() -> None:
    """Active runs should still keep the newest openable track pinned."""
    client = FakeClient()
    client.get_queue = lambda: {
        "active": JobSnapshot(
            job_id="job-123",
            state="running",
            position=0,
            processed_count=1,
            total_urls=3,
            phase="importing",
            detail="Current Track",
        ),
        "queued": [],
    }
    client.recent = lambda limit=3: [
        ("u1", "Andromedik", "Forever", "downloaded", "2026-03-16T19:33:00"),
    ]
    controller = MenuBarController(
        client,
        read_clipboard_text=lambda: "",
        frontmost_bundle_id=lambda: None,
        read_chrome_url=lambda: None,
    )

    snapshot = controller.refresh_snapshot()

    assert snapshot.primary_track is not None
    assert snapshot.primary_track.label() == "Andromedik - Forever"


def test_controller_deduplicated_submission_reports_already_running(monkeypatch) -> None:
    """Repeated menu submissions should surface daemon-side dedupe feedback."""
    client = FakeClient(
        inspections=[
            _inspection(
                actionable=[
                    {
                        "normalized_url": "https://youtube.com/watch?v=abc",
                        "source": "youtube",
                        "kind": "track",
                    }
                ]
            )
        ]
    )
    client.submit_job = lambda request: JobSubmission(
        snapshot=JobSnapshot(job_id="job-123", state="running", position=0, total_urls=1),
        deduplicated=True,
    )
    controller = MenuBarController(
        client,
        read_clipboard_text=lambda: "https://youtube.com/watch?v=abc",
        frontmost_bundle_id=lambda: None,
        read_chrome_url=lambda: None,
    )
    monkeypatch.setattr(controller, "_start_watch", lambda *_args, **_kwargs: None)

    outcome = controller.sync_from_inputs()

    assert outcome.submitted is True
    assert outcome.message == "Already running from clipboard"


def test_refresh_snapshot_offers_open_track_action_from_recent_history() -> None:
    """The newest openable history row should surface as a top-level action."""
    client = FakeClient()
    client.recent = lambda limit=3: [
        ("u1", "Andromedik", "Forever", "downloaded", "2026-03-16T19:33:00"),
        ("u2", "Artist 2", "Track 2", "failed", "2026-03-16T19:30:00"),
    ]
    controller = MenuBarController(
        client,
        read_clipboard_text=lambda: "",
        frontmost_bundle_id=lambda: None,
        read_chrome_url=lambda: None,
    )

    snapshot = controller.refresh_snapshot()

    assert snapshot.primary_track is not None
    assert snapshot.primary_track.label() == "Andromedik - Forever"


def test_menu_rows_do_not_render_redundant_title_line() -> None:
    """Visible menu rows should start with useful status detail, not the snapshot title."""
    snapshot = SimpleNamespace(
        title="Idle",
        detail="Ready to sync",
        status_lines=["↓ Downloading · Test Artist - Test Track"],
        primary_track=None,
        history=[],
    )

    rows = menubar_app.MenuBarApplication._menu_rows(SimpleNamespace(), snapshot)

    assert rows[0].title == "Ready to sync"
    assert all(row.title != "Idle" for row in rows if row.kind != "separator")


def test_refresh_snapshot_formats_failed_history_without_unknown_placeholders() -> None:
    """Blank failed rows should describe the failure, not render Unknown placeholders."""
    client = FakeClient()
    client.recent = lambda limit=3: [
        ("u1", "", "", "failed", "2026-03-16T19:33:00"),
    ]
    controller = MenuBarController(
        client,
        read_clipboard_text=lambda: "",
        frontmost_bundle_id=lambda: None,
        read_chrome_url=lambda: None,
    )

    snapshot = controller.refresh_snapshot()

    assert snapshot.history[0].status_label == "Failed"
    assert snapshot.history[0].row_label == "Previous attempt failed before track was identified"


def test_summary_feedback_prefers_single_report_note() -> None:
    """Single-item runs should surface the detailed pipeline note when available."""
    feedback = _summary_feedback(
        {
            "stats": {
                "downloaded": 1,
                "in_library": 0,
                "cached": 0,
                "skipped": 0,
                "failed": 0,
            }
        },
        reports=[
            {
                "status": "downloaded",
                "note": "In library folder (Music.app notification failed) -> Track.m4a",
            }
        ],
    )

    assert feedback == "In library folder (Music.app notification failed) -> Track.m4a"


def test_snapshot_feedback_includes_mixed_counts() -> None:
    """Snapshot fallback should preserve failures when some items downloaded."""
    feedback = _snapshot_feedback(
        SimpleNamespace(
            downloaded_count=1,
            in_library_count=0,
            cached_count=0,
            skipped_count=0,
            failed_count=2,
            last_message=None,
            job_id="job-123",
            state="completed",
        )
    )

    assert feedback == "1 track downloaded/imported; 2 tracks failed"


def test_snapshot_feedback_falls_back_to_failed_counts() -> None:
    """Snapshot fallback should not claim success for failed runs."""
    feedback = _snapshot_feedback(
        SimpleNamespace(
            downloaded_count=0,
            in_library_count=0,
            cached_count=0,
            skipped_count=0,
            failed_count=2,
            last_message=None,
            job_id="job-123",
            state="completed",
        )
    )

    assert feedback == "2 tracks failed"


def test_mouse_event_mask_uses_mouse_up_only(monkeypatch) -> None:
    """Status-item clicks should dispatch once per user click."""
    monkeypatch.setattr(
        menubar_app,
        "AppKit",
        SimpleNamespace(
            NSEventMaskLeftMouseUp=0x01,
            NSEventMaskRightMouseUp=0x04,
        ),
    )

    assert menubar_app._mouse_event_mask() == 0x05


def test_install_refresh_timer_includes_event_tracking_mode(monkeypatch) -> None:
    """Menu refresh timer should keep firing while the dropdown is open."""

    class FakeTimer:
        def __init__(self) -> None:
            self.tolerance = None

        def setTolerance_(self, value) -> None:  # noqa: N802
            self.tolerance = value

    class FakeNSTimer:
        @staticmethod
        def timerWithTimeInterval_target_selector_userInfo_repeats_(
            interval,
            target,
            selector,
            _user_info,
            repeats,
        ):
            assert interval == 0.5
            assert selector == "refresh:"
            assert repeats is True
            assert target is marker
            return fake_timer

    class FakeRunLoop:
        def __init__(self) -> None:
            self.modes = []

        def addTimer_forMode_(self, timer, mode) -> None:  # noqa: N802
            self.modes.append((timer, mode))

    marker = object()
    fake_timer = FakeTimer()
    fake_run_loop = FakeRunLoop()

    monkeypatch.setattr(menubar_app, "NSTimer", FakeNSTimer)
    monkeypatch.setattr(
        menubar_app,
        "NSRunLoop",
        SimpleNamespace(mainRunLoop=lambda: fake_run_loop),
    )
    monkeypatch.setattr(
        menubar_app,
        "AppKit",
        SimpleNamespace(
            NSRunLoopCommonModes="common",
            NSEventTrackingRunLoopMode="tracking",
        ),
    )

    timer = menubar_app._install_refresh_timer(marker, interval_seconds=0.5)

    assert timer is fake_timer
    assert fake_timer.tolerance == 0.2
    assert fake_run_loop.modes == [(fake_timer, "common"), (fake_timer, "tracking")]


def test_status_item_title_for_phase_uses_phase_glyphs() -> None:
    """Status-item title should reflect queue/running/error state concisely."""
    assert status_item_title_for_phase(phase="downloading", queued=False, errored=False) == "que↓"
    assert status_item_title_for_phase(phase="importing", queued=False, errored=False) == "que↗"
    assert status_item_title_for_phase(phase=None, queued=True, errored=False) == "que…"
    assert status_item_title_for_phase(phase=None, queued=False, errored=True) == "que!"


def test_diagnostics_log_writes_json_line(monkeypatch, tmp_path) -> None:
    """Diagnostics logging should append structured JSON lines."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    log_diagnostic("menu_snapshot", title="Idle", detail="Ready")

    text = diagnostics_log_path().read_text(encoding="utf-8")
    assert "\"event\": \"menu_snapshot\"" in text
    assert "\"title\": \"Idle\"" in text


def test_open_track_in_music_uses_exact_and_contains_fallback(monkeypatch) -> None:
    """Music reveal should try exact lookup and then a looser contains fallback."""
    captured = {}

    def fake_run(args, check, capture_output, text, timeout):
        captured["script"] = args[2]
        return SimpleNamespace(returncode=0, stdout="1\n")

    monkeypatch.setattr(menubar_music.subprocess, "run", fake_run)

    ok = menubar_music.open_track_in_music("Rameses B", "Open Your Eyes (L Plus Remix)")

    assert ok is True
    assert 'whose name is "Open Your Eyes (L Plus Remix)"' in captured["script"]
    assert 'whose name contains "Open Your Eyes (L Plus Remix)"' in captured["script"]
