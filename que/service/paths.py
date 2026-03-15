"""Filesystem paths used by the local que daemon."""
from __future__ import annotations

import os
from pathlib import Path


def _xdg_state_home() -> Path:
    """Return XDG_STATE_HOME, defaulting to ~/.local/state."""
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")


def service_state_dir() -> Path:
    """Return the state directory used by the daemon."""
    return _xdg_state_home() / "que"


def service_socket_path() -> Path:
    """Return the daemon Unix socket path."""
    return service_state_dir() / "daemon.sock"


def service_pid_path() -> Path:
    """Return the daemon PID/lock file path."""
    return service_state_dir() / "daemon.pid"

