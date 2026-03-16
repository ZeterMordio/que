"""Regression tests for shared intake inspection."""
from __future__ import annotations

from que.intake import inspect_input, normalize_url, parse_actionable_urls, target_from_url


def test_inspect_input_classifies_supported_unsupported_and_invalid_urls() -> None:
    """Intake should partition supported, unsupported, and unknown URLs."""
    inspection = inspect_input(
        text="""
https://youtube.com/watch?v=abc&utm_source=feed
https://soundcloud.com/artist/track
https://open.spotify.com/track/123
https://www.queup.net/room/demo
https://example.com/nope
""".strip()
    )

    assert [item.source for item in inspection.actionable_urls] == ["youtube", "soundcloud"]
    assert [item.normalized_url for item in inspection.actionable_urls] == [
        "https://youtube.com/watch?v=abc",
        "https://soundcloud.com/artist/track",
    ]
    assert [item.kind for item in inspection.actionable_urls] == ["track", "track"]
    assert [item.source for item in inspection.recognized_unsupported] == ["spotify", "queup"]
    assert [item.reason for item in inspection.invalid] == ["unrecognized_url"]


def test_parse_actionable_urls_deduplicates_normalized_urls() -> None:
    """Supported URLs should dedupe after normalization."""
    urls = parse_actionable_urls(
        """
https://youtube.com/watch?v=abc&si=123
https://youtube.com/watch?v=abc
https://open.spotify.com/track/123
""".strip()
    )

    assert urls == ["https://youtube.com/watch?v=abc"]


def test_inspect_input_accepts_explicit_candidates() -> None:
    """Explicit candidates should be classified without free-form text parsing."""
    inspection = inspect_input(
        candidates=[
            "https://soundcloud.com/artist/track",
            "https://open.spotify.com/playlist/123",
            "https://example.com/x",
        ]
    )

    assert [item.source for item in inspection.actionable_urls] == ["soundcloud"]
    assert [item.source for item in inspection.recognized_unsupported] == ["spotify"]
    assert inspection.invalid[0].normalized_url == "https://example.com/x"


def test_normalize_url_strips_tracking_parameters() -> None:
    """Known tracking parameters should not change cache identity."""
    assert normalize_url("https://youtube.com/watch?v=abc&si=123&feature=share") == (
        "https://youtube.com/watch?v=abc"
    )


def test_normalize_url_strips_playlist_context_from_watch_urls() -> None:
    """Watch URLs should stay single-track targets even inside mixes/playlists."""
    assert normalize_url(
        "https://www.youtube.com/watch?v=abc&list=RD123&index=28&pp=ygUIc29uZw%3D%3D"
    ) == "https://www.youtube.com/watch?v=abc"
    assert normalize_url("https://youtu.be/abc?list=RD123&index=28") == "https://youtu.be/abc"


def test_normalize_url_keeps_only_playlist_identity_for_playlist_urls() -> None:
    """Pure playlist URLs should keep playlist identity but drop extra context."""
    assert normalize_url(
        "https://www.youtube.com/playlist?list=PL123&si=abc&index=7"
    ) == "https://www.youtube.com/playlist?list=PL123"


def test_target_from_url_classifies_watch_and_playlist_intent() -> None:
    """Shared intake targets should distinguish single tracks from playlists."""
    watch = target_from_url("https://www.youtube.com/watch?v=abc&list=RD123&index=28")
    playlist = target_from_url("https://www.youtube.com/playlist?list=PL123&si=abc")

    assert watch.kind == "track"
    assert watch.normalized_url == "https://www.youtube.com/watch?v=abc"
    assert playlist.kind == "playlist"
    assert playlist.normalized_url == "https://www.youtube.com/playlist?list=PL123"
