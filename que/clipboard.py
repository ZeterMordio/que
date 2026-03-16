"""Clipboard helpers for macOS media intake."""
from __future__ import annotations

import subprocess

from .intake import normalize_url, parse_actionable_urls

__all__ = ["normalize_url", "read_clipboard", "parse_urls", "get_urls_from_clipboard"]


def read_clipboard() -> str:
    """Read clipboard text on macOS using pbpaste."""
    try:
        result = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5)
        return result.stdout
    except Exception:
        return ""


def parse_urls(text: str) -> list[str]:
    """
    Extract all supported media URLs from a block of text.
    Handles plain URL lists (one per line), CSV rows, and mixed content.
    Deduplicates while preserving order.
    """
    return parse_actionable_urls(text)


def get_urls_from_clipboard() -> list[str]:
    """Read clipboard and return all supported media URLs found."""
    return parse_urls(read_clipboard())
