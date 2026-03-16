"""Helpers for running ``yt-dlp`` from CLI and packaged app contexts.

Packaged macOS app runtimes can leak Python bootstrap environment variables
such as ``PYTHONHOME`` into child processes. ``yt-dlp`` is itself a Python
program, so inherited bundle-specific variables can make it import against the
wrong standard library and fail before any network work starts.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

_PYTHON_ENV_KEYS = (
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHONEXECUTABLE",
    "__PYVENV_LAUNCHER__",
)


def yt_dlp_subprocess_env() -> dict[str, str]:
    """Return a sanitized environment for ``yt-dlp`` subprocesses.

    Returns:
        Copy of the current process environment with Python bootstrap variables
        removed and common user/Homebrew bin paths prepended.
    """
    env = os.environ.copy()
    for key in _PYTHON_ENV_KEYS:
        env.pop(key, None)

    preferred_paths = [
        str(Path.home() / ".local" / "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
    ]
    existing_parts = [part for part in env.get("PATH", "").split(os.pathsep) if part]
    merged_parts: list[str] = []
    for part in [*preferred_paths, *existing_parts]:
        if part and part not in merged_parts:
            merged_parts.append(part)
    env["PATH"] = os.pathsep.join(merged_parts)
    return env


def run_yt_dlp(command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    """Run one ``yt-dlp`` command with a bundle-safe environment.

    Args:
        command: Full ``yt-dlp`` argv vector.
        timeout: Subprocess timeout in seconds.

    Returns:
        The completed subprocess result.
    """
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=yt_dlp_subprocess_env(),
    )
