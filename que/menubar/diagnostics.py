"""Structured diagnostics logging for the que menu-bar client."""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from ..service.paths import service_state_dir

_LOG_LOCK = threading.RLock()
_MAX_LOG_BYTES = 512 * 1024


def diagnostics_log_path() -> Path:
    """Return the diagnostics log path for the menu-bar client.

    Returns:
        Path to the JSON-lines diagnostics log file.
    """
    return service_state_dir() / "menubar.log"


def log_diagnostic(event: str, **payload: Any) -> None:
    """Append one JSON-lines diagnostics event.

    Args:
        event: Stable event name.
        **payload: JSON-safe event payload values.
    """
    path = diagnostics_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    record = {
        "timestamp": datetime.now().isoformat(),
        "event": event,
        "payload": _json_safe(payload),
    }

    with _LOG_LOCK:
        _rotate_if_needed(path)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True))
            handle.write("\n")


def _rotate_if_needed(path: Path) -> None:
    """Rotate the diagnostics log when it grows too large."""
    if not path.exists() or path.stat().st_size < _MAX_LOG_BYTES:
        return

    rotated_path = path.with_suffix(".log.1")
    try:
        rotated_path.unlink()
    except FileNotFoundError:
        pass
    path.replace(rotated_path)


def _json_safe(value: Any) -> Any:
    """Convert nested values into JSON-safe primitives."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "__dict__"):
        return _json_safe(vars(value))
    return str(value)
