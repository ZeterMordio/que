"""macOS helpers for frontmost-app detection and Google Chrome tab access."""
from __future__ import annotations

import subprocess


class ChromeAutomationError(RuntimeError):
    """Raised when Google Chrome automation is unavailable."""


def frontmost_bundle_id() -> str | None:
    """Return the frontmost application bundle identifier when available."""
    try:
        from AppKit import NSWorkspace  # type: ignore
    except Exception:
        return None

    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        return None
    bundle_id = app.bundleIdentifier()
    return str(bundle_id) if bundle_id else None


def read_google_chrome_active_tab_url() -> str | None:
    """Read the active Google Chrome tab URL via AppleScript.

    Returns:
        Active tab URL text, or ``None`` when no front window/tab is available.

    Raises:
        ChromeAutomationError: When Chrome access is denied or fails.
    """
    script = """
tell application "Google Chrome"
    if (count of windows) is 0 then
        return ""
    end if
    return URL of active tab of front window
end tell
""".strip()

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:
        raise ChromeAutomationError(str(exc)) from exc

    if result.returncode != 0:
        stderr = result.stderr.strip() or "Automation denied"
        raise ChromeAutomationError(stderr)

    url = result.stdout.strip()
    return url or None
