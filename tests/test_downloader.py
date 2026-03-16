"""Regression tests for yt-dlp download command construction."""
from __future__ import annotations

from types import SimpleNamespace

from que import downloader


def test_download_track_prefers_audio_only_m4a_stream(tmp_path, monkeypatch) -> None:
    """M4A downloads should explicitly prefer audio-only M4A streams."""
    calls: list[list[str]] = []

    def fake_run(command, *, timeout):
        calls.append(command)
        (tmp_path / "track.m4a").write_text("audio", encoding="utf-8")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(downloader, "run_yt_dlp", fake_run)

    attempt = downloader.download_track(
        "https://youtube.com/watch?v=abc",
        tmp_path,
        audio_format="m4a",
    )

    assert attempt.path == tmp_path / "track.m4a"
    assert len(calls) == 1
    command = calls[0]
    assert "-f" in command
    assert "bestaudio[ext=m4a]/bestaudio" in command
    assert "--cookies-from-browser" not in command
    assert "--postprocessor-args" not in command


def test_download_track_adds_ffmpeg_thread_cap_when_requested(
    tmp_path, monkeypatch
) -> None:
    """Multi-worker runs should be able to cap ffmpeg postprocessor threads."""
    calls: list[list[str]] = []

    def fake_run(command, *, timeout):
        calls.append(command)
        (tmp_path / "track.mp3").write_text("audio", encoding="utf-8")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(downloader, "run_yt_dlp", fake_run)

    attempt = downloader.download_track(
        "https://youtube.com/watch?v=abc",
        tmp_path,
        audio_format="mp3",
        ffmpeg_threads=1,
    )

    assert attempt.path == tmp_path / "track.mp3"
    assert len(calls) == 1
    command = calls[0]
    assert "bestaudio/best" in command
    assert "--postprocessor-args" in command
    assert "ExtractAudio+ffmpeg:-threads 1" in command


def test_download_track_retries_with_browser_cookies_on_failure(
    tmp_path, monkeypatch
) -> None:
    """Failed public downloads should retry with browser cookies."""
    calls: list[list[str]] = []

    def fake_run(command, *, timeout):
        calls.append(command)
        if len(calls) == 1:
            return SimpleNamespace(
                returncode=1,
                stderr="ERROR: Sign in to confirm you're not a bot",
            )
        (tmp_path / "track.m4a").write_text("audio", encoding="utf-8")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(downloader, "run_yt_dlp", fake_run)

    attempt = downloader.download_track(
        "https://youtube.com/watch?v=abc",
        tmp_path,
        audio_format="m4a",
    )

    assert attempt.path == tmp_path / "track.m4a"
    assert len(calls) == 2
    assert "--cookies-from-browser" not in calls[0]
    assert "bestaudio[ext=m4a]/bestaudio" in calls[0]
    assert "--cookies-from-browser" in calls[1]
    assert "bestaudio/best" in calls[1]


def test_run_command_uses_bundle_safe_yt_dlp_wrapper(monkeypatch) -> None:
    """Download subprocesses should go through the sanitized yt-dlp runner."""
    captured: list[tuple[list[str], int]] = []

    def fake_run(command, *, timeout):
        captured.append((command, timeout))
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(downloader, "run_yt_dlp", fake_run)

    returncode, error = downloader._run_command(["yt-dlp", "--version"], timeout=12)

    assert (returncode, error) == (0, "")
    assert captured == [(["yt-dlp", "--version"], 12)]
