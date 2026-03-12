"""
downloader.py — downloads a single track via yt-dlp into a staging directory.

Downloads go to ~/Downloads/que_staging/ (configurable) rather than the home
directory directly, so it's easy to track what was just fetched and clean up
after a failed import.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

_AUDIO_EXTS = {".m4a", ".mp3", ".flac", ".aac", ".ogg", ".wav", ".aiff", ".aif"}


@dataclass
class DownloadAttempt:
    """Structured result for a yt-dlp download attempt."""

    path: Path | None
    error: str | None = None


def _format_selector(audio_format: str, prefer_same_container: bool) -> str:
    """Return the yt-dlp format selector for the requested audio output."""
    if audio_format == "m4a" and prefer_same_container:
        # Reason: prefer YouTube's existing AAC/M4A stream when available.
        # This avoids downloading full video tracks and often reduces ffmpeg work.
        return "bestaudio[ext=m4a]/bestaudio"
    return "bestaudio/best"


def _build_command(
    url: str,
    staging_dir: Path,
    audio_format: str,
    ffmpeg_threads: int | None,
    *,
    use_browser_cookies: bool,
    prefer_same_container: bool,
) -> list[str]:
    """Return the yt-dlp command for one download attempt."""
    command = [
        "yt-dlp",
        "-f",
        _format_selector(audio_format, prefer_same_container),
        "-x",
        "--audio-format",
        audio_format,
        "--audio-quality",
        "0",
    ]
    if ffmpeg_threads is not None:
        command.extend(
            [
                "--postprocessor-args",
                f"ExtractAudio+ffmpeg:-threads {ffmpeg_threads}",
            ]
        )
    if use_browser_cookies:
        command.extend(["--cookies-from-browser", "chrome"])
    command.extend(
        [
            "--no-playlist",
            "--output",
            str(staging_dir / "%(title)s.%(ext)s"),
            "--quiet",
            "--no-warnings",
            "--no-progress",
            url,
        ]
    )
    return command


def _run_command(command: list[str], timeout: int) -> tuple[int, str]:
    """Run yt-dlp and return `(returncode, stderr_message)`."""
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode == 0:
        return 0, ""
    stderr = result.stderr.strip()
    message = stderr.splitlines()[-1] if stderr else f"yt-dlp exited {result.returncode}"
    return result.returncode, message


def download_track(
    url: str,
    staging_dir: Path,
    audio_format: str = "m4a",
    timeout: int = 600,
    ffmpeg_threads: int | None = None,
) -> DownloadAttempt:
    """
    Download a single track to staging_dir.

    Returns a structured result containing the downloaded file path or an error.
    Uses a before/after directory snapshot to identify the new file
    (more reliable than parsing yt-dlp stdout).

    timeout is 600s (10 min) by default — long mixes and slow connections
    can easily exceed 300s.
    """
    staging_dir.mkdir(parents=True, exist_ok=True)

    before: set[Path] = set(staging_dir.iterdir())

    try:
        primary_command = _build_command(
            url,
            staging_dir,
            audio_format,
            ffmpeg_threads,
            use_browser_cookies=False,
            prefer_same_container=True,
        )
        returncode, error = _run_command(primary_command, timeout)

        if returncode != 0:
            # Reason: browser cookies currently push some YouTube videos onto
            # SABR/web_safari clients that only expose muxed formats. Keep the
            # fast public path cookie-free, then retry with cookies only when
            # the initial download fails.
            fallback_command = _build_command(
                url,
                staging_dir,
                audio_format,
                ffmpeg_threads,
                use_browser_cookies=True,
                prefer_same_container=False,
            )
            fallback_returncode, fallback_error = _run_command(fallback_command, timeout)
            if fallback_returncode != 0:
                if fallback_error and fallback_error != error:
                    return DownloadAttempt(
                        path=None,
                        error=f"{error}; cookie retry: {fallback_error}",
                    )
                return DownloadAttempt(path=None, error=fallback_error or error)
        else:
            error = None
    except subprocess.TimeoutExpired:
        return DownloadAttempt(path=None, error=f"Timed out after {timeout}s")
    except Exception as exc:
        return DownloadAttempt(path=None, error=str(exc))

    after: set[Path] = set(staging_dir.iterdir())
    new_files = after - before

    audio_files = [f for f in new_files if f.suffix.lower() in _AUDIO_EXTS]
    if not audio_files:
        return DownloadAttempt(path=None, error="yt-dlp finished but no audio file was produced")

    # Return the most recently modified audio file (handles edge cases)
    return DownloadAttempt(
        path=sorted(audio_files, key=lambda f: f.stat().st_mtime, reverse=True)[0]
    )
