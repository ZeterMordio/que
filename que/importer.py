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
from dataclasses import dataclass
from pathlib import Path

from rapidfuzz import fuzz

_UNSAFE_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_TRACK_NUM_RE = re.compile(r"^\d{1,3}[\s\.\-]+")
_EXT_RE = re.compile(r"\.(m4a|mp3|flac|aac|ogg|wav|aiff?)$", re.IGNORECASE)
_PARENS_RE = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_AUDIO_EXTS = {".m4a", ".mp3", ".flac", ".aac", ".ogg", ".wav", ".aiff", ".aif"}


@dataclass(frozen=True)
class ImportResult:
    """Outcome of one Apple Music import attempt.

    Attributes:
        success: True when the file reached its intended final state.
        music_app_imported: True when Music.app accepted the file.
        message: User-facing status text for CLI/menu surfaces.
        final_path: Final on-disk location when the file was moved successfully.
    """

    success: bool
    music_app_imported: bool
    message: str
    final_path: Path | None = None


def _safe_name(s: str) -> str:
    """Replace filesystem-unsafe characters with underscores."""
    return _UNSAFE_RE.sub("_", s).strip(" .")


def _escape_applescript_string(value: str) -> str:
    """Escape a string for safe inclusion inside AppleScript string literals."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _normalize_match_text(value: str) -> str:
    """Normalize track-like text for recovery matching."""
    normalized = _TRACK_NUM_RE.sub("", value.strip().lower())
    normalized = _EXT_RE.sub("", normalized)
    normalized = _PARENS_RE.sub("", normalized)
    return _NON_ALNUM_RE.sub(" ", normalized).strip()


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


def _find_existing_library_file(
    library_path: Path,
    artist: str,
    title: str,
) -> Path | None:
    """Find a likely already-moved file under the import destination."""
    search_roots = []
    if artist:
        artist_dir = library_path / _safe_name(artist)
        if artist_dir.exists():
            search_roots.append(artist_dir)
    if library_path.exists():
        search_roots.append(library_path)

    normalized_title = _normalize_match_text(title)
    best_match: tuple[int, Path] | None = None

    for root in search_roots:
        for candidate in root.rglob("*"):
            if not candidate.is_file() or candidate.suffix.lower() not in _AUDIO_EXTS:
                continue
            score = int(
                fuzz.token_set_ratio(
                    normalized_title,
                    _normalize_match_text(candidate.name),
                )
            )
            if best_match is None or score > best_match[0]:
                best_match = (score, candidate)

    if best_match and best_match[0] >= 92:
        return best_match[1]
    return None


def retry_music_app_import(
    library_path: Path,
    artist: str,
    title: str,
    playlist_name: str | None = None,
) -> ImportResult:
    """Retry the Music.app add step for an already-moved file.

    Args:
        library_path: Root import destination used by que.
        artist: Resolved track artist for narrowing the search.
        title: Resolved track title for fuzzy filename matching.
        playlist_name: Optional Apple Music playlist target.

    Returns:
        ImportResult describing whether an existing file was found and whether
        Music.app accepted it.
    """
    existing_file = _find_existing_library_file(library_path, artist, title)
    if existing_file is None:
        return ImportResult(
            success=False,
            music_app_imported=False,
            message="No existing library file found for retry",
        )

    music_ok = _osascript_add(existing_file, playlist_name=playlist_name)
    if music_ok:
        if playlist_name:
            return ImportResult(
                success=True,
                music_app_imported=True,
                message=(
                    f'Imported existing library file via Music.app and added to "{playlist_name}"'
                    f" -> {existing_file.name}"
                ),
                final_path=existing_file,
            )
        return ImportResult(
            success=True,
            music_app_imported=True,
            message=f"Imported existing library file via Music.app → {existing_file.name}",
            final_path=existing_file,
        )

    if playlist_name:
        return ImportResult(
            success=False,
            music_app_imported=False,
            message=(
                "Existing library file found but Music.app playlist import failed "
                f'for "{playlist_name}"'
                f" -> {existing_file.name}"
            ),
            final_path=existing_file,
        )
    return ImportResult(
        success=False,
        music_app_imported=False,
        message=(
            "Existing library file found but Music.app notification failed "
            f"-> {existing_file.name}"
        ),
        final_path=existing_file,
    )


def import_to_apple_music(
    file_path: Path,
    artist: str,
    library_path: Path,
    use_music_app: bool = True,
    playlist_name: str | None = None,
) -> ImportResult:
    """Import a downloaded audio file into Apple Music.

    Args:
        file_path: Downloaded media file in staging.
        artist: Resolved artist name for folder placement.
        library_path: Root folder for imported media.
        use_music_app: Whether to notify Music.app after the move.
        playlist_name: Optional Apple Music playlist target.

    Returns:
        ImportResult with the final file path plus Music.app add outcome.
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
        return ImportResult(
            success=False,
            music_app_imported=False,
            message=f"Could not move file to library folder: {e}",
        )

    # ── Step 2: notify Music.app ─────────────────────────────────────────────
    if use_music_app and dest_path:
        music_ok = _osascript_add(dest_path, playlist_name=playlist_name)
        if music_ok:
            if playlist_name:
                return ImportResult(
                    success=True,
                    music_app_imported=True,
                    message=(
                        f'Imported via Music.app and added to "{playlist_name}"'
                        f" -> {dest_path.name}"
                    ),
                    final_path=dest_path,
                )
            return ImportResult(
                success=True,
                music_app_imported=True,
                message=f"Imported via Music.app → {dest_path.name}",
                final_path=dest_path,
            )
        else:
            # File is already in the right folder; Music.app notification failed
            if playlist_name:
                return ImportResult(
                    success=True,
                    music_app_imported=False,
                    message=(
                        "In library folder (Music.app playlist import failed "
                        f'for "{playlist_name}")'
                        f" -> {dest_path.name}"
                    ),
                    final_path=dest_path,
                )
            return ImportResult(
                success=True,
                music_app_imported=False,
                message=f"In library folder (Music.app notification failed) -> {dest_path.name}",
                final_path=dest_path,
            )

    if playlist_name:
        return ImportResult(
            success=True,
            music_app_imported=False,
            message=(
                f'Moved to library folder (playlist "{playlist_name}" '
                "requires Music.app integration)"
                f" -> {dest_path.name}"
            ),
            final_path=dest_path,
        )
    return ImportResult(
        success=True,
        music_app_imported=False,
        message=f"Moved to library folder → {dest_path.name}",
        final_path=dest_path,
    )
