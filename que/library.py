"""
library.py — Apple Music library scanning and track matching.

Architecture note
-----------------
LibraryChecker is defined as a Protocol so the fuzzy implementation
can be swapped for an AI-powered one with zero changes to the rest
of the codebase.  When you're ready to add semantic understanding:

    class AILibraryChecker:
        def is_in_library(self, artist: str, title: str) -> CheckResult:
            # call your model here
            ...

    checker = AILibraryChecker(...)   # drop-in replacement

Fuzzy matching strategy
-----------------------
Apple Music folder structure:  Artist / Album / Track.m4a

We score artist and title independently via rapidfuzz token_set_ratio
(handles word-order differences, "feat." variants, etc.) then combine:
    score = 0.4 * artist_score + 0.6 * title_score

When artist info is missing we fall back to title-only, but cap the
maximum possible score at ~70 so it stays below the default threshold
of 85 — meaning we will download rather than wrongly skip.

The threshold default (85) is intentionally high so that edge-case
or partially-matched titles still get downloaded.  Lower = more
aggressive downloading; raise it only if you're getting duplicates.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Protocol, runtime_checkable

from rapidfuzz import fuzz

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class Track:
    path: Path
    artist: str   # inferred from parent directory name
    album: str    # inferred from grandparent directory name
    title: str    # normalised from filename


@dataclass
class CheckResult:
    in_library: bool
    confidence: float       # 0–100
    matched_track: Optional[Track]
    reason: str


# ---------------------------------------------------------------------------
# LibraryChecker Protocol — swap this out for AILibraryChecker later
# ---------------------------------------------------------------------------

@runtime_checkable
class LibraryChecker(Protocol):
    def is_in_library(self, artist: str, title: str) -> CheckResult:
        ...


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

_TRACK_NUM_RE = re.compile(r"^\d{1,3}[\s\.\-]+")   # "01 ", "02 - ", "3. "
_EXT_RE = re.compile(r"\.(m4a|mp3|flac|aac|ogg|wav|aiff?)$", re.IGNORECASE)
_FEAT_RE = re.compile(r"\s*[\(\[](feat|ft)\.?\s+[^\)\]]+[\)\]]", re.IGNORECASE)
_PARENS_RE = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")  # generic (...)


def _normalize(s: str) -> str:
    """Strip track numbers, extensions, featured artist tags, and lowercase."""
    s = _TRACK_NUM_RE.sub("", s)
    s = _EXT_RE.sub("", s)
    s = _FEAT_RE.sub("", s)
    return s.strip().lower()


def _normalize_title_aggressive(s: str) -> str:
    """Also strip all parenthesised/bracketed suffixes."""
    s = _normalize(s)
    s = _PARENS_RE.sub("", s)
    return s.strip()


# ---------------------------------------------------------------------------
# Library indexing
# ---------------------------------------------------------------------------

_AUDIO_EXTS = {".m4a", ".mp3", ".flac", ".aac", ".ogg", ".wav", ".aiff", ".aif"}


def _index_one_path(library_path: Path) -> List[Track]:
    """
    Walk a single directory and return Track objects.

    Handles two layouts automatically:
      Structured:  <root>/<Artist>/<Album>/<track.m4a>   → artist & album inferred
      Flat:        <root>/<track.m4a>                    → artist/album left empty,
                                                           title-only matching applies
    """
    tracks: List[Track] = []
    if not library_path.exists():
        return tracks

    for root, _dirs, files in os.walk(library_path):
        rel_parts = Path(root).relative_to(library_path).parts
        # rel_parts == () means files sit directly in the root (flat layout)
        artist = rel_parts[0] if len(rel_parts) >= 1 else ""
        album  = rel_parts[1] if len(rel_parts) >= 2 else ""

        for fname in files:
            if Path(fname).suffix.lower() in _AUDIO_EXTS:
                tracks.append(
                    Track(
                        path=Path(root) / fname,
                        artist=artist,
                        album=album,
                        title=_normalize(fname),
                    )
                )
    return tracks


def build_music_app_index() -> List[Track]:
    """
    Query Music.app via osascript (JXA) for the complete track list.

    This is the preferred source of truth over filesystem scanning:
    it reflects exactly what Apple Music considers "in your library",
    regardless of where the files are stored on disk.

    Returns an empty list if Music.app is unavailable or the query fails.
    """
    # JXA script: fetch name + artist for every track in the library
    script = (
        'const music = Application("Music");'
        'try {'
        '  const tracks = music.libraryPlaylists[0].tracks();'
        '  const out = [];'
        '  for (let i = 0; i < tracks.length; i++) {'
        '    out.push({n: tracks[i].name(), a: tracks[i].artist()});'
        '  }'
        '  JSON.stringify(out);'
        '} catch(e) { "[]"; }'
    )
    try:
        result = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", script],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout.strip())
        return [
            Track(
                path=Path("/"),          # no file path available from Music.app query
                artist=item.get("a", ""),
                album="",
                title=_normalize(item.get("n", "")),
            )
            for item in data
            if item.get("n")
        ]
    except Exception:
        return []


def build_library_index(library_paths: "List[Path] | Path") -> List[Track]:
    """
    Build a unified track index from one or more library roots.
    Accepts either a single Path or a list of Paths.
    """
    if isinstance(library_paths, Path):
        library_paths = [library_paths]

    tracks: List[Track] = []
    for p in library_paths:
        tracks.extend(_index_one_path(p))
    return tracks


# ---------------------------------------------------------------------------
# FuzzyLibraryChecker
# ---------------------------------------------------------------------------

class FuzzyLibraryChecker:
    """
    Deterministic fuzzy-match checker backed by rapidfuzz.

    Parameters
    ----------
    library_path : Path
        Root of the Apple Music library.
    threshold : int
        Minimum combined score (0–100) required to consider a track as
        "already in library".  Default 85 — err on the side of downloading.
    """

    def __init__(self, library_paths: "List[Path] | Path", threshold: int = 85):
        self.threshold = threshold
        # Try Music.app first — it's the definitive source of truth.
        # Fall back to filesystem scanning if Music.app is unavailable.
        music_app_tracks = build_music_app_index()
        if music_app_tracks:
            self._tracks = music_app_tracks
            self._source = "Music.app"
        else:
            self._tracks = build_library_index(library_paths)
            self._source = "filesystem"

    def is_in_library(self, artist: str, title: str) -> CheckResult:
        if not self._tracks:
            return CheckResult(
                in_library=False,
                confidence=0.0,
                matched_track=None,
                reason="Library is empty or path not found — will download",
            )

        norm_title = _normalize(title)
        norm_title_agg = _normalize_title_aggressive(title)
        norm_artist = _normalize(artist)

        best_score = 0.0
        best_track: Optional[Track] = None

        for track in self._tracks:
            # Title matching — try both normalisation levels, take the better one
            ts1 = fuzz.token_set_ratio(norm_title, track.title)
            ts2 = fuzz.token_set_ratio(norm_title_agg, track.title)
            title_score = max(ts1, ts2)

            if norm_artist and track.artist:
                artist_score = fuzz.token_set_ratio(
                    norm_artist, _normalize(track.artist)
                )
                score = 0.4 * artist_score + 0.6 * title_score
            else:
                # No artist info on one or both sides.
                # Cap at 70 so we stay below the default threshold of 85
                # and lean towards downloading when uncertain.
                score = min(0.75 * title_score, 70.0)

            if score > best_score:
                best_score = score
                best_track = track

        in_library = best_score >= self.threshold

        if in_library:
            reason = (
                f"Matched '{best_track.artist} – {best_track.title}' "  # type: ignore[union-attr]
                f"(confidence {best_score:.1f})"
            )
        else:
            reason = (
                f"Best score {best_score:.1f} < threshold {self.threshold} "
                f"— will download"
            )

        return CheckResult(
            in_library=in_library,
            confidence=best_score,
            matched_track=best_track if in_library else None,
            reason=reason,
        )
