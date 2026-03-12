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


@dataclass
class CacheEntry:
    url: str
    title: str
    artist: str
    # 'in_library' | 'downloaded' | 'failed' | 'skipped'
    status: str
    processed_at: datetime


@dataclass
class RunEntry:
    """Aggregated metrics for one que run."""

    run_id: int
    started_at: datetime
    finished_at: datetime | None
    run_mode: str
    jobs: int
    total_urls: int
    playlist_name: str | None
    dry_run: bool
    preflight_seconds: float | None
    download_phase_seconds: float | None
    total_seconds: float | None
    queued_downloads: int
    downloaded_count: int
    in_library_count: int
    cached_count: int
    skipped_count: int
    failed_count: int
    download_failed_count: int
    import_failed_count: int
    downloaded_bytes: int
    average_download_bytes_per_second: float | None


class Cache:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    def _ensure_columns(self, table: str, columns: dict[str, str]) -> None:
        """Add missing columns to an existing table."""
        existing = {
            row[1]
            for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for name, definition in columns.items():
            if name in existing:
                continue
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

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
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS processing_runs (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at            TEXT NOT NULL,
                finished_at           TEXT,
                run_mode              TEXT NOT NULL DEFAULT 'normal',
                jobs                  INTEGER NOT NULL,
                total_urls            INTEGER NOT NULL,
                playlist_name         TEXT,
                dry_run               INTEGER NOT NULL DEFAULT 0,
                preflight_seconds     REAL,
                download_phase_seconds REAL,
                total_seconds         REAL,
                queued_downloads      INTEGER NOT NULL DEFAULT 0,
                downloaded_count      INTEGER NOT NULL DEFAULT 0,
                in_library_count      INTEGER NOT NULL DEFAULT 0,
                cached_count          INTEGER NOT NULL DEFAULT 0,
                skipped_count         INTEGER NOT NULL DEFAULT 0,
                failed_count          INTEGER NOT NULL DEFAULT 0,
                download_failed_count INTEGER NOT NULL DEFAULT 0,
                import_failed_count   INTEGER NOT NULL DEFAULT 0,
                downloaded_bytes      INTEGER NOT NULL DEFAULT 0,
                average_download_bytes_per_second REAL
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS processing_run_items (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id                INTEGER NOT NULL,
                item_index            INTEGER NOT NULL,
                url                   TEXT NOT NULL,
                artist                TEXT NOT NULL DEFAULT '',
                title                 TEXT NOT NULL DEFAULT '',
                status                TEXT NOT NULL,
                note                  TEXT NOT NULL DEFAULT '',
                started_at            TEXT,
                queued_at             TEXT,
                download_started_at   TEXT,
                download_finished_at  TEXT,
                committed_at          TEXT,
                queue_wait_seconds    REAL,
                download_seconds      REAL,
                tag_seconds           REAL,
                import_seconds        REAL,
                total_item_seconds    REAL,
                file_size_bytes       INTEGER,
                download_bytes_per_second REAL,
                failure_stage         TEXT NOT NULL DEFAULT '',
                worker_name           TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(run_id) REFERENCES processing_runs(id) ON DELETE CASCADE,
                UNIQUE(run_id, item_index)
            )
        """)
        self._ensure_columns(
            "processing_runs",
            {
                "download_failed_count": "INTEGER NOT NULL DEFAULT 0",
                "import_failed_count": "INTEGER NOT NULL DEFAULT 0",
                "downloaded_bytes": "INTEGER NOT NULL DEFAULT 0",
                "average_download_bytes_per_second": "REAL",
                "run_mode": "TEXT NOT NULL DEFAULT 'normal'",
            },
        )
        self._ensure_columns(
            "processing_run_items",
            {
                "download_bytes_per_second": "REAL",
                "failure_stage": "TEXT NOT NULL DEFAULT ''",
            },
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_processing_run_items_run_id "
            "ON processing_run_items(run_id, item_index)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_processing_runs_started_at "
            "ON processing_runs(started_at DESC)"
        )
        self.conn.commit()

    def get(self, url: str) -> CacheEntry | None:
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

    def start_run(
        self,
        *,
        total_urls: int,
        run_mode: str,
        jobs: int,
        dry_run: bool,
        playlist_name: str | None,
    ) -> int:
        """Insert a new processing run and return its ID."""
        cursor = self.conn.execute(
            "INSERT INTO processing_runs "
            "(started_at, run_mode, jobs, total_urls, playlist_name, dry_run) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                datetime.now().isoformat(),
                run_mode,
                jobs,
                total_urls,
                playlist_name,
                int(dry_run),
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def record_run_item(
        self,
        *,
        run_id: int,
        item_index: int,
        url: str,
        artist: str,
        title: str,
        status: str,
        note: str,
        started_at: str | None = None,
        queued_at: str | None = None,
        download_started_at: str | None = None,
        download_finished_at: str | None = None,
        committed_at: str | None = None,
        queue_wait_seconds: float | None = None,
        download_seconds: float | None = None,
        tag_seconds: float | None = None,
        import_seconds: float | None = None,
        total_item_seconds: float | None = None,
        file_size_bytes: int | None = None,
        download_bytes_per_second: float | None = None,
        failure_stage: str = "",
        worker_name: str = "",
    ) -> None:
        """Persist one per-item processing record for a run."""
        self.conn.execute(
            "INSERT OR REPLACE INTO processing_run_items "
            "("
            "run_id, item_index, url, artist, title, status, note, started_at, queued_at, "
            "download_started_at, download_finished_at, committed_at, queue_wait_seconds, "
            "download_seconds, tag_seconds, import_seconds, total_item_seconds, "
            "file_size_bytes, download_bytes_per_second, failure_stage, worker_name"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                item_index,
                url,
                artist,
                title,
                status,
                note,
                started_at,
                queued_at,
                download_started_at,
                download_finished_at,
                committed_at,
                queue_wait_seconds,
                download_seconds,
                tag_seconds,
                import_seconds,
                total_item_seconds,
                file_size_bytes,
                download_bytes_per_second,
                failure_stage,
                worker_name,
            ),
        )
        self.conn.commit()

    def finish_run(
        self,
        *,
        run_id: int,
        preflight_seconds: float,
        download_phase_seconds: float,
        total_seconds: float,
        queued_downloads: int,
        downloaded_count: int,
        in_library_count: int,
        cached_count: int,
        skipped_count: int,
        failed_count: int,
        download_failed_count: int,
        import_failed_count: int,
        downloaded_bytes: int,
        average_download_bytes_per_second: float | None,
    ) -> None:
        """Finalize an existing run with aggregate timing and count metrics."""
        self.conn.execute(
            "UPDATE processing_runs SET "
            "finished_at = ?, "
            "preflight_seconds = ?, "
            "download_phase_seconds = ?, "
            "total_seconds = ?, "
            "queued_downloads = ?, "
            "downloaded_count = ?, "
            "in_library_count = ?, "
            "cached_count = ?, "
            "skipped_count = ?, "
            "failed_count = ?, "
            "download_failed_count = ?, "
            "import_failed_count = ?, "
            "downloaded_bytes = ?, "
            "average_download_bytes_per_second = ? "
            "WHERE id = ?",
            (
                datetime.now().isoformat(),
                preflight_seconds,
                download_phase_seconds,
                total_seconds,
                queued_downloads,
                downloaded_count,
                in_library_count,
                cached_count,
                skipped_count,
                failed_count,
                download_failed_count,
                import_failed_count,
                downloaded_bytes,
                average_download_bytes_per_second,
                run_id,
            ),
        )
        self.conn.commit()

    def recent(self, limit: int = 50, status_filter: str | None = None):
        query = "SELECT url, artist, title, status, processed_at FROM processed_urls"
        params: list = []
        if status_filter:
            query += " WHERE status = ?"
            params.append(status_filter)
        query += " ORDER BY processed_at DESC LIMIT ?"
        params.append(limit)
        return self.conn.execute(query, params).fetchall()

    def recent_runs(self, limit: int = 20) -> list[RunEntry]:
        """Return recent aggregate run metrics."""
        rows = self.conn.execute(
            "SELECT id, started_at, finished_at, run_mode, jobs, total_urls, "
            "playlist_name, dry_run, "
            "preflight_seconds, download_phase_seconds, total_seconds, queued_downloads, "
            "downloaded_count, in_library_count, cached_count, skipped_count, failed_count, "
            "download_failed_count, import_failed_count, downloaded_bytes, "
            "average_download_bytes_per_second "
            "FROM processing_runs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            RunEntry(
                run_id=row[0],
                started_at=datetime.fromisoformat(row[1]),
                finished_at=datetime.fromisoformat(row[2]) if row[2] else None,
                run_mode=row[3],
                jobs=row[4],
                total_urls=row[5],
                playlist_name=row[6],
                dry_run=bool(row[7]),
                preflight_seconds=row[8],
                download_phase_seconds=row[9],
                total_seconds=row[10],
                queued_downloads=row[11],
                downloaded_count=row[12],
                in_library_count=row[13],
                cached_count=row[14],
                skipped_count=row[15],
                failed_count=row[16],
                download_failed_count=row[17],
                import_failed_count=row[18],
                downloaded_bytes=row[19],
                average_download_bytes_per_second=row[20],
            )
            for row in rows
        ]

    def close(self):
        self.conn.close()


class _NullCache:
    """Drop-in replacement when --no-cache is passed."""

    def get(self, url: str) -> None:
        return None

    def set(self, *args):
        pass

    def start_run(self, **kwargs) -> int | None:
        return None

    def record_run_item(self, **kwargs) -> None:
        return None

    def finish_run(self, **kwargs) -> None:
        return None

    def recent(self, **kwargs):
        return []

    def recent_runs(self, **kwargs):
        return []

    def close(self):
        pass
