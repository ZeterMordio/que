"""Job execution entrypoints for the daemon-backed service layer."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..cache import Cache, _NullCache
from ..config import load_config
from ..pipeline import ProcessResult, process_urls
from ..resolver import expand_playlist, is_playlist_url
from .models import JobRequest

EventHandler = Callable[[str, str | None, dict[str, Any]], None]


def _emit(
    handler: EventHandler | None,
    event_type: str,
    message: str | None = None,
    **payload: Any,
) -> None:
    """Emit an event when a handler is configured."""
    if handler is None:
        return
    handler(event_type, message, payload)


def _expand_urls(request: JobRequest, event_handler: EventHandler | None) -> list[str]:
    """Expand playlist URLs into per-track URLs for the pipeline."""
    expanded: list[str] = []
    for url in request.urls:
        if is_playlist_url(url):
            _emit(event_handler, "log", "  Expanding playlist…", url=url)
            tracks = expand_playlist(url)
            expanded.extend(tracks)
            _emit(
                event_handler,
                "playlist_expanded",
                f"  📋 Playlist expanded → [bold]{len(tracks)}[/bold] tracks",
                url=url,
                expanded_count=len(tracks),
            )
            continue
        expanded.append(url)

    _emit(
        event_handler,
        "expanded_urls_ready",
        f"Found [bold]{len(expanded)}[/bold] URL(s)"
        + (" [dim](dry-run)[/dim]" if request.dry_run else ""),
        total_urls=len(expanded),
    )
    return expanded


def execute_job(
    request: JobRequest,
    event_handler: EventHandler | None = None,
) -> ProcessResult:
    """Run one job request through the existing pipeline."""
    request.validate()
    config = load_config()
    if request.threshold_override is not None:
        config.fuzzy_threshold = request.threshold_override
    if request.playlist_name and not config.use_music_app:
        # Reason: playlist placement still depends on Music.app scripting.
        config.use_music_app = True
        _emit(
            event_handler,
            "log",
            "[yellow]Playlist import requires Music.app integration; "
            "enabling it for this run.[/yellow]",
        )

    cache = Cache(config.cache_path) if request.benchmark or request.use_cache else _NullCache()
    try:
        expanded_urls = _expand_urls(request, event_handler)
        return process_urls(
            expanded_urls,
            dry_run=request.dry_run,
            config=config,
            cache=cache,
            playlist_name=request.playlist_name,
            jobs=request.jobs,
            benchmark=request.benchmark,
            event_handler=event_handler,
        )
    finally:
        cache.close()
