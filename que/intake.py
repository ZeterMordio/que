"""Shared media intake inspection and URL intent classification.

The intake layer is the single source of truth for:

- URL normalization for cache and dedupe identity
- media intent classification (`track` vs `playlist`)
- supported vs recognized-but-unsupported source detection

Clients should submit canonical URLs to the daemon and avoid inventing their own
playlist heuristics.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

# Tracking/session parameters that should be stripped before cache lookup.
_STRIP_PARAMS = {
    "si",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
    "ref",
    "feature",
}
_YOUTUBE_TRACK_ONLY_PARAMS = {"list", "index", "pp", "start", "t"}
_YOUTUBE_PLAYLIST_KEEP_PARAMS = {"list"}

_GENERIC_URL_RE = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)
_SOURCE_PATTERNS: tuple[tuple[str, re.Pattern[str], bool], ...] = (
    (
        "youtube",
        re.compile(r"^(?:https?://)?(?:www\.)?youtu(?:\.be|be\.com)/\S+$", re.IGNORECASE),
        True,
    ),
    (
        "soundcloud",
        re.compile(r"^(?:https?://)?(?:api\.)?soundcloud\.com/\S+$", re.IGNORECASE),
        True,
    ),
    (
        "spotify",
        re.compile(
            r"^(?:https?://)?open\.spotify\.com/(?:track|album|playlist|artist)/\S+$",
            re.IGNORECASE,
        ),
        False,
    ),
    (
        "queup",
        re.compile(r"^(?:https?://)?(?:www\.)?queup\.net/\S+$", re.IGNORECASE),
        False,
    ),
)
_YOUTUBE_TRACK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(?:https?://)?(?:www\.)?youtube\.com/watch\?", re.IGNORECASE),
    re.compile(r"^(?:https?://)?(?:www\.)?youtu\.be/\S+$", re.IGNORECASE),
)
_YOUTUBE_PLAYLIST_PATTERN = re.compile(
    r"^(?:https?://)?(?:www\.)?youtube\.com/playlist\?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class MediaTarget:
    """Canonical media target derived from one URL.

    Attributes:
        raw_url: Original URL text.
        normalized_url: Canonical URL used for cache and dedupe checks.
        source: Recognized media source name.
        kind: Intent classification for shared backend behavior.
    """

    raw_url: str
    normalized_url: str
    source: str
    kind: str

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-safe dictionary."""
        return {
            "raw_url": self.raw_url,
            "normalized_url": self.normalized_url,
            "source": self.source,
            "kind": self.kind,
        }


def normalize_url(url: str) -> str:
    """Normalize one URL for cache and dedupe checks.

    Args:
        url: Raw URL text.

    Returns:
        URL text with known tracking parameters removed when parsing succeeds.
    """
    cleaned = url.strip().rstrip(".,;)]}")
    try:
        parsed = urlparse(cleaned)
        filtered = _filtered_query_params(parsed)
        normalized = parsed._replace(query=urlencode(filtered, doseq=True))
        return urlunparse(normalized)
    except Exception:
        return cleaned


@dataclass(frozen=True)
class IntakeItem:
    """One classified URL candidate."""

    raw_url: str
    normalized_url: str
    source: str
    kind: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-safe dictionary."""
        return {
            "raw_url": self.raw_url,
            "normalized_url": self.normalized_url,
            "source": self.source,
            "kind": self.kind,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class IntakeInspection:
    """Classification result for one input payload."""

    actionable_urls: list[IntakeItem]
    recognized_unsupported: list[IntakeItem]
    invalid: list[IntakeItem]

    def to_dict(self) -> dict[str, list[dict[str, str]]]:
        """Return a JSON-safe dictionary."""
        return {
            "actionable_urls": [item.to_dict() for item in self.actionable_urls],
            "recognized_unsupported": [item.to_dict() for item in self.recognized_unsupported],
            "invalid": [item.to_dict() for item in self.invalid],
        }


def parse_actionable_urls(text: str) -> list[str]:
    """Return actionable media URLs from raw text.

    Args:
        text: Raw clipboard or command-line text.

    Returns:
        Normalized actionable URLs only, preserving first-seen order.
    """
    inspection = inspect_input(text=text)
    return [item.normalized_url for item in inspection.actionable_urls]


def parse_media_targets(text: str) -> list[MediaTarget]:
    """Return canonical actionable media targets from raw text.

    Args:
        text: Free-form text that may contain supported URLs.

    Returns:
        Canonical actionable targets, preserving first-seen order.
    """
    inspection = inspect_input(text=text)
    return [
        MediaTarget(
            raw_url=item.raw_url,
            normalized_url=item.normalized_url,
            source=item.source,
            kind=item.kind,
        )
        for item in inspection.actionable_urls
    ]


def target_from_url(url: str) -> MediaTarget:
    """Build one canonical target from a raw URL.

    Args:
        url: Raw media URL text.

    Returns:
        Canonical target carrying source and `track`/`playlist` intent.
    """
    normalized = normalize_url(url)
    source, _actionable_now = _classify_source(normalized)
    return MediaTarget(
        raw_url=url,
        normalized_url=normalized,
        source=source,
        kind=classify_target_kind(normalized, source=source),
    )


def inspect_input(
    *,
    text: str | None = None,
    candidates: Iterable[str] | None = None,
) -> IntakeInspection:
    """Inspect raw intake text and explicit URL candidates.

    Args:
        text: Free-form text that may contain URLs.
        candidates: Explicit URL candidates supplied out-of-band.

    Returns:
        Classified actionable, unsupported, and invalid URLs.
    """
    if text is not None and not isinstance(text, str):
        raise ValueError("text must be a string")
    if candidates is not None and not isinstance(candidates, (list, tuple)):
        raise ValueError("candidates must be a list of URL strings")

    seen: set[str] = set()
    actionable: list[IntakeItem] = []
    unsupported: list[IntakeItem] = []
    invalid: list[IntakeItem] = []

    for raw_url in _iter_candidates(text=text, candidates=candidates):
        normalized = normalize_url(raw_url)
        dedupe_key = normalized.lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        source, actionable_now = _classify_source(normalized)
        if source == "unknown":
            invalid.append(
                IntakeItem(
                    raw_url=raw_url,
                    normalized_url=normalized,
                    source="unknown",
                    kind="unknown",
                    reason="unrecognized_url",
                )
            )
            continue

        kind = classify_target_kind(normalized, source=source)
        item = IntakeItem(
            raw_url=raw_url,
            normalized_url=normalized,
            source=source,
            kind=kind,
            reason="supported_media_url" if actionable_now else "recognized_but_unsupported",
        )
        if actionable_now:
            actionable.append(item)
        else:
            unsupported.append(item)

    return IntakeInspection(
        actionable_urls=actionable,
        recognized_unsupported=unsupported,
        invalid=invalid,
    )


def _iter_candidates(
    *,
    text: str | None,
    candidates: Iterable[str] | None,
) -> Iterable[str]:
    """Yield candidate URLs from text and explicit values."""
    if text:
        for match in _GENERIC_URL_RE.finditer(text):
            yield match.group(0)
    if candidates:
        for candidate in candidates:
            stripped = str(candidate).strip()
            if stripped:
                yield stripped


def _classify_source(url: str) -> tuple[str, bool]:
    """Return source name and whether the backend supports it today."""
    for source, pattern, actionable in _SOURCE_PATTERNS:
        if pattern.match(url):
            return source, actionable
    return "unknown", False


def classify_target_kind(url: str, *, source: str | None = None) -> str:
    """Return the shared backend intent for one canonical URL.

    Args:
        url: Canonicalized media URL.
        source: Optional recognized source, when already known.

    Returns:
        `track`, `playlist`, or `unknown`.
    """
    resolved_source = source or _classify_source(url)[0]
    parsed = urlparse(url)
    if resolved_source == "youtube":
        if _is_youtube_playlist(parsed):
            return "playlist"
        if _is_youtube_track(parsed):
            return "track"
    if resolved_source == "soundcloud":
        return "track"
    if resolved_source in {"spotify", "queup"}:
        return "track"
    return "unknown"


def _filtered_query_params(parsed) -> dict[str, list[str]]:
    """Return canonical query parameters for a parsed URL."""
    filtered = {
        key: value
        for key, value in parse_qs(parsed.query).items()
        if key not in _STRIP_PARAMS
    }
    if _is_youtube_playlist(parsed):
        return {
            key: value
            for key, value in filtered.items()
            if key in _YOUTUBE_PLAYLIST_KEEP_PARAMS
        }
    if _is_youtube_track(parsed):
        return {
            key: value
            for key, value in filtered.items()
            if key not in _YOUTUBE_TRACK_ONLY_PARAMS
        }
    return filtered


def _is_youtube_playlist(parsed) -> bool:
    """Return True for explicit YouTube playlist targets."""
    host = parsed.netloc.lower().removeprefix("www.")
    return host == "youtube.com" and parsed.path.rstrip("/") == "/playlist"


def _is_youtube_track(parsed) -> bool:
    """Return True for single-track YouTube watch or youtu.be URLs."""
    host = parsed.netloc.lower().removeprefix("www.")
    if host == "youtube.com":
        return parsed.path == "/watch" and "v" in parse_qs(parsed.query)
    if host == "youtu.be":
        return bool(parsed.path.strip("/"))
    return False
