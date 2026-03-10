"""
downloader.py — downloads a single track via yt-dlp into a staging directory.

Downloads go to ~/Downloads/que_staging/ (configurable) rather than the home
directory directly, so it's easy to track what was just fetched and clean up
after a failed import.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

_AUDIO_EXTS = {".m4a", ".mp3", ".flac", ".aac", ".ogg", ".wav", ".aiff", ".aif"}


def download_track(
    url: str,
    staging_dir: Path,
    audio_format: str = "m4a",
    timeout: int = 600,
) -> Optional[Path]:
    """
    Download a single track to staging_dir.

    Returns the Path of the downloaded audio file, or None on failure/timeout.
    Uses a before/after directory snapshot to identify the new file
    (more reliable than parsing yt-dlp stdout).

    timeout is 600s (10 min) by default — long mixes and slow connections
    can easily exceed 300s.
    """
    staging_dir.mkdir(parents=True, exist_ok=True)

    output_template = str(staging_dir / "%(title)s.%(ext)s")
    before: set[Path] = set(staging_dir.iterdir())

    try:
        result = subprocess.run(
            [
                "yt-dlp",
                "-x",
                "--audio-format", audio_format,
                "--audio-quality", "0",          # best quality
                "--cookies-from-browser", "chrome",
                "--no-playlist",
                "--output", output_template,
                "--quiet",
                "--no-warnings",
                "--no-progress",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None

    if result.returncode != 0:
        return None

    after: set[Path] = set(staging_dir.iterdir())
    new_files = after - before

    audio_files = [f for f in new_files if f.suffix.lower() in _AUDIO_EXTS]
    if not audio_files:
        return None

    # Return the most recently modified audio file (handles edge cases)
    return sorted(audio_files, key=lambda f: f.stat().st_mtime, reverse=True)[0]
