"""
cache.py — SQLite-backed URL cache at ~/.local/share/que/cache.db.

Stores the outcome of each processed URL so re-running que on the
same playlist is instant and doesn't re-fetch metadata or re-check
the library for known tracks.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class CacheEntry:
    url: str
    title: str
    artist: str
    # 'in_library' | 'downloaded' | 'failed' | 'skipped'
    status: str
    processed_at: datetime


class Cache:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self._init_schema()

    def _init_schema(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS processed_urls (
                url          TEXT PRIMARY KEY,
                title        TEXT NOT NULL DEFAULT '',
                artist       TEXT NOT NULL DEFAULT '',
                status       TEXT NOT NULL,
                processed_at TEXT NOT NULL
            )
        """)
        self.conn.commit()

    def get(self, url: str) -> Optional[CacheEntry]:
        row = self.conn.execute(
            "SELECT url, title, artist, status, processed_at "
            "FROM processed_urls WHERE url = ?",
            (url,),
        ).fetchone()
        if row:
            return CacheEntry(
                url=row[0],
                title=row[1],
                artist=row[2],
                status=row[3],
                processed_at=datetime.fromisoformat(row[4]),
            )
        return None

    def set(self, url: str, title: str, artist: str, status: str):
        self.conn.execute(
            "INSERT OR REPLACE INTO processed_urls "
            "(url, title, artist, status, processed_at) VALUES (?, ?, ?, ?, ?)",
            (url, title, artist, status, datetime.now().isoformat()),
        )
        self.conn.commit()

    def recent(self, limit: int = 50, status_filter: Optional[str] = None):
        query = "SELECT url, artist, title, status, processed_at FROM processed_urls"
        params: list = []
        if status_filter:
            query += " WHERE status = ?"
            params.append(status_filter)
        query += " ORDER BY processed_at DESC LIMIT ?"
        params.append(limit)
        return self.conn.execute(query, params).fetchall()

    def close(self):
        self.conn.close()


class _NullCache:
    """Drop-in replacement when --no-cache is passed."""
    def get(self, url: str) -> None:
        return None
    def set(self, *args):
        pass
    def recent(self, **kwargs):
        return []
    def close(self):
        pass
