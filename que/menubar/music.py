"""Apple Music and completion-notification helpers for the menu-bar client."""
from __future__ import annotations

import subprocess


def open_track_in_music(artist: str, title: str) -> bool:
    """Reveal a matching track in Music.app.

    Args:
        artist: Track artist text.
        title: Track title text.

    Returns:
        True when Music.app found and revealed the track.
    """
    escaped_title = _escape(title)
    escaped_artist = _escape(artist)
    script = (
        'tell application "Music"\n'
        "    set matchingTracks to "
        f'(every track of library playlist 1 whose name is "{escaped_title}" '
        f'and artist is "{escaped_artist}")\n'
        "    if (count of matchingTracks) is 0 then\n"
        "        set matchingTracks to "
        f'(every track of library playlist 1 whose name contains "{escaped_title}" '
        f'and artist contains "{escaped_artist}")\n'
        "    end if\n"
        "    if (count of matchingTracks) is 0 then\n"
        '        return "0"\n'
        "    end if\n"
        "    reveal item 1 of matchingTracks\n"
        "    activate\n"
        '    return "1"\n'
        "end tell"
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return False
    return result.returncode == 0 and result.stdout.strip() == "1"


def notify_sync_result(title: str, message: str, *, success: bool) -> None:
    """Show a best-effort macOS notification plus a light sound.

    Args:
        title: Notification title.
        message: Notification body.
        success: Whether the result was successful.
    """
    sound_name = "Glass" if success else "Basso"
    escaped_title = _escape(title)
    escaped_message = _escape(message)
    script = (
        f'display notification "{escaped_message}" '
        f'with title "Que Menu" subtitle "{escaped_title}" '
        f'sound name "{sound_name}"'
    )
    try:
        subprocess.run(
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return


def _escape(value: str) -> str:
    """Escape text for AppleScript string literals."""
    return value.replace("\\", "\\\\").replace('"', '\\"')
