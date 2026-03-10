"""
tagger.py — applies ID3/MP4 metadata tags to downloaded audio files.

Uses mutagen for .m4a (MP4 atoms) and .mp3 (ID3).  Tagging is best-effort:
if mutagen isn't installed or tagging fails, the download still proceeds.

Why tag before importing?
  Apple Music reads tags on import.  Without correct tags the track will
  appear under "Unknown Artist / Unknown Album" and won't match existing
  library entries on re-runs.
"""
from __future__ import annotations

from pathlib import Path

try:
    from mutagen.mp4 import MP4
    from mutagen.id3 import ID3, TIT2, TPE1, TALB, ID3NoHeaderError
    _MUTAGEN = True
except ImportError:
    _MUTAGEN = False


def tag_file(
    path: Path,
    artist: str,
    title: str,
    album: str = "",
) -> bool:
    """
    Write artist / title / album tags to an audio file.
    Returns True on success, False if tagging was skipped or failed.
    """
    if not _MUTAGEN:
        return False

    try:
        suffix = path.suffix.lower()

        if suffix == ".m4a":
            audio = MP4(str(path))
            if artist:
                audio["\xa9ART"] = [artist]
            if title:
                audio["\xa9nam"] = [title]
            if album:
                audio["\xa9alb"] = [album]
            audio.save()
            return True

        if suffix == ".mp3":
            try:
                tags = ID3(str(path))
            except ID3NoHeaderError:
                tags = ID3()
            if artist:
                tags["TPE1"] = TPE1(encoding=3, text=artist)
            if title:
                tags["TIT2"] = TIT2(encoding=3, text=title)
            if album:
                tags["TALB"] = TALB(encoding=3, text=album)
            tags.save(str(path))
            return True

    except Exception:
        pass

    return False
