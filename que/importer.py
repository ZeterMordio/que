"""
importer.py — moves a downloaded file into the Apple Music library folder,
then optionally notifies Music.app via AppleScript.

Strategy: "folder first, osascript second"
  1. Always move the file into the library folder structure first.
     This is reliable and leaves the file safe even if step 2 fails.
  2. Attempt to tell Music.app about the new file via osascript.
     This is best-effort — a failure is logged but does NOT abort.

Apple Music folder layout created:
  <library_path>/<Artist>/<Unknown Album>/<filename>

You can change the album folder name in config if desired.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

_UNSAFE_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_name(s: str) -> str:
    """Replace filesystem-unsafe characters with underscores."""
    return _UNSAFE_RE.sub("_", s).strip(" .")


def _escape_applescript_string(value: str) -> str:
    """Escape a string for safe inclusion inside AppleScript string literals."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _osascript_add(file_path: Path, playlist_name: str | None = None) -> bool:
    """
    Tell Music.app to add a file via AppleScript.
    Returns True on success, False on any error.
    """
    posix = _escape_applescript_string(str(file_path.resolve()))
    playlist = _escape_applescript_string(playlist_name or "")
    script = f'''
tell application "Music"
    set addedItems to add POSIX file "{posix}"
    if "{playlist}" is not "" then
        if exists user playlist "{playlist}" then
            set targetPlaylist to user playlist "{playlist}"
        else
            set targetPlaylist to make new user playlist with properties {{name:"{playlist}"}}
        end if

        if class of addedItems is list then
            repeat with addedTrack in addedItems
                duplicate addedTrack to targetPlaylist
            end repeat
        else
            duplicate addedItems to targetPlaylist
        end if
    end if
end tell
'''.strip()
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=20,
        )
        return result.returncode == 0
    except Exception:
        return False


def import_to_apple_music(
    file_path: Path,
    artist: str,
    library_path: Path,
    use_music_app: bool = True,
    playlist_name: str | None = None,
) -> tuple[bool, str]:
    """
    Import a downloaded audio file into the Apple Music library.

    Returns (success: bool, message: str).
    """
    dest_path: Path | None = None

    # ── Step 1: move file into library folder ────────────────────────────────
    artist_dir = _safe_name(artist) if artist else "Unknown Artist"
    dest_dir = library_path / artist_dir / "Unknown Album"
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / file_path.name
        # Avoid overwriting an existing file with the same name
        if dest_path.exists():
            stem = file_path.stem
            suffix = file_path.suffix
            counter = 1
            while dest_path.exists():
                dest_path = dest_dir / f"{stem} ({counter}){suffix}"
                counter += 1
        shutil.move(str(file_path), str(dest_path))
    except Exception as e:
        return False, f"Could not move file to library folder: {e}"

    # ── Step 2: notify Music.app ─────────────────────────────────────────────
    if use_music_app and dest_path:
        music_ok = _osascript_add(dest_path, playlist_name=playlist_name)
        if music_ok:
            if playlist_name:
                return True, (
                    f'Imported via Music.app and added to "{playlist_name}"'
                    f" -> {dest_path.name}"
                )
            return True, f"Imported via Music.app → {dest_path.name}"
        else:
            # File is already in the right folder; Music.app notification failed
            if playlist_name:
                return True, (
                    f'In library folder (Music.app playlist import failed for "{playlist_name}")'
                    f" -> {dest_path.name}"
                )
            return True, f"In library folder (Music.app notification failed) -> {dest_path.name}"

    if playlist_name:
        return True, (
            f'Moved to library folder (playlist "{playlist_name}" '
            "requires Music.app integration)"
            f" -> {dest_path.name}"
        )
    return True, f"Moved to library folder → {dest_path.name}"
