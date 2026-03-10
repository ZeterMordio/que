"""
clipboard.py — reads the macOS clipboard and extracts supported media URLs.

Supported sources:
  - YouTube  (youtu.be/..., youtube.com/watch?v=...)
  - SoundCloud  (soundcloud.com/..., api.soundcloud.com/tracks/...)
  - Spotify  (open.spotify.com/track/...)  — for future yt-dlp/spotdl support
"""
from __future__ import annotations

import re
import subprocess
from typing import List
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse

# Tracking/session parameters that should be stripped before cache lookup
_STRIP_PARAMS = {
    "si",           # YouTube share session token
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "ref", "feature",
}


def normalize_url(url: str) -> str:
    """Strip known tracking parameters so the same video is always one cache key."""
    try:
        parsed = urlparse(url)
        filtered = {k: v for k, v in parse_qs(parsed.query).items() if k not in _STRIP_PARAMS}
        cleaned = parsed._replace(query=urlencode(filtered, doseq=True))
        return urlunparse(cleaned)
    except Exception:
        return url

# Matches the URL formats produced by the QueUp playlist exporter
_URL_RE = re.compile(
    r"https?://"
    r"(?:"
    r"(?:www\.)?youtu(?:\.be|be\.com)/\S+"
    r"|(?:api\.)?soundcloud\.com/\S+"
    r"|open\.spotify\.com/track/\S+"
    r")",
    re.IGNORECASE,
)


def read_clipboard() -> str:
    """Read clipboard text on macOS using pbpaste."""
    try:
        result = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5)
        return result.stdout
    except Exception:
        return ""


def parse_urls(text: str) -> List[str]:
    """
    Extract all supported media URLs from a block of text.
    Handles plain URL lists (one per line), CSV rows, and mixed content.
    Deduplicates while preserving order.
    """
    seen: set[str] = set()
    urls: List[str] = []

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        for match in _URL_RE.finditer(line):
            url = normalize_url(match.group(0).rstrip(".,;)"))
            if url not in seen:
                seen.add(url)
                urls.append(url)

    return urls


def get_urls_from_clipboard() -> List[str]:
    """Read clipboard and return all supported media URLs found."""
    return parse_urls(read_clipboard())
