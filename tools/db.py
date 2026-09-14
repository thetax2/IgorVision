"""
core/db.py
==========
SQLite index for the compare scan.

Stores the file state (size, mtime, optional SHA-256) and EXIF
metadata per source pair (shooting days + backup), so that rescans,
"Refresh" and EXIF reading can run incrementally and a scan
cancellation does not lose any captured data.

Important: a sqlite3 connection must not be shared across threads.
Worker threads therefore each open their own instance on the same
DB file (WAL mode allows parallel access by multiple connections).
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_DB_NAME = "index.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS file_index (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_key TEXT NOT NULL,
    tree TEXT NOT NULL,
    full_path TEXT NOT NULL,
    name_lower TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime REAL NOT NULL,
    sha256 TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE (scan_key, tree, full_path)
);
CREATE INDEX IF NOT EXISTS idx_fi_tree ON file_index (scan_key, tree);
CREATE INDEX IF NOT EXISTS idx_fi_name ON file_index (scan_key, tree, name_lower);

CREATE TABLE IF NOT EXISTS exif_cache (
    scan_key TEXT NOT NULL,
    full_path TEXT NOT NULL,
    size INTEGER,
    mtime REAL,
    date_str TEXT,
    date_source TEXT,
    model TEXT,
    lens TEXT,
    focal TEXT,
    media TEXT,
    PRIMARY KEY (scan_key, full_path)
);
"""


def db_file_name() -> str:
    return _DB_NAME


def scan_key_for(import_root: Path, backup_dir: Path) -> str:
    """Stable key for a source pair (normalised path strings)."""
    base = f"{str(import_root).lower()}|{str(backup_dir).lower()}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class ScanDB:
    """SQLite connection for a scan (one instance per thread)."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.execute("PRAGMA busy_timeout=5000;")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:
            pass

    def commit(self) -> None:
        self._conn.commit()

    # ── Dateiindex ──────────────────────────────────────────────────────

    def load_tree(self, scan_key: str, tree: str) -> dict[str, dict]:
        """Bestehende DB-Zeilen eines Baums: full_path → {size, mtime, sha256}."""
        rows = self._conn.execute(
            "SELECT full_path, size, mtime, sha256 FROM file_index "
            "WHERE scan_key = ? AND tree = ?",
            (scan_key, tree),
        ).fetchall()
        return {
            r["full_path"]: {
                "size": r["size"], "mtime": r["mtime"], "sha256": r["sha256"],
            }
            for r in rows
        }

    def sync_tree(self, scan_key: str, tree: str, entries: list[tuple]) -> None:
        """
        Bring the DB up to date with the current disk state (one transaction).

        `entries`: List[(full_path, name_lower, size, mtime, sha256)] –
        exactly the files that now exist on disk. Rows for files that
        no longer exist are deleted; new and changed files are upserted.
        """
        now = _now()
        paths = [e[0] for e in entries]
        with self._conn:
            if paths:
                # Remove deleted files – in chunks because of the
                # SQLite parameter limit (max. 999 variables per statement).
                for i in range(0, len(paths), 500):
                    chunk = paths[i:i + 500]
                    placeholders = ",".join("?" * len(chunk))
                    self._conn.execute(
                        f"DELETE FROM file_index "
                        f"WHERE scan_key = ? AND tree = ? AND full_path NOT IN ({placeholders})",
                        [scan_key, tree, *chunk],
                    )
                self._conn.executemany(
                    """
                    INSERT INTO file_index
                        (scan_key, tree, full_path, name_lower, size, mtime, sha256, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (scan_key, tree, full_path) DO UPDATE SET
                        name_lower = excluded.name_lower,
                        size = excluded.size,
                        mtime = excluded.mtime,
                        sha256 = excluded.sha256,
                        updated_at = excluded.updated_at
                    """,
                    [(scan_key, tree, p, n, s, m, h, now) for (p, n, s, m, h) in entries],
                )
            else:
                # Tree empty → delete the old state completely
                self._conn.execute(
                    "DELETE FROM file_index WHERE scan_key = ? AND tree = ?",
                    (scan_key, tree),
                )

    # ── EXIF-Cache ──────────────────────────────────────────────────────

    def get_exif(self, scan_key: str, full_path: str,
                 size: int, mtime: float) -> Optional[dict]:
        """
        EXIF cache lookup. Returns None if the file has changed since
        the cache (size or mtime differ).
        """
        row = self._conn.execute(
            "SELECT size, mtime, date_str, date_source, model, lens, focal, media "
            "FROM exif_cache WHERE scan_key = ? AND full_path = ?",
            (scan_key, full_path),
        ).fetchone()
        if row is None:
            return None
        if row["size"] != size or row["mtime"] != mtime:
            return None
        return {
            "date_str": row["date_str"] or "",
            "date_source": row["date_source"] or "",
            "model": row["model"] or "",
            "lens": row["lens"] or "",
            "focal": row["focal"] or "",
            "media": row["media"] or "",
        }

    def upsert_exif_many(self, scan_key: str, items: list[tuple]) -> None:
        """
        Store EXIF results in a single transaction.

        `items`: List[(full_path, size, mtime, meta_dict)] where
        meta_dict = {date_str, date_source, model, lens, focal, media}.
        """
        if not items:
            return
        with self._conn:
            self._conn.executemany(
                """
                INSERT INTO exif_cache
                    (scan_key, full_path, size, mtime,
                     date_str, date_source, model, lens, focal, media)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (scan_key, full_path) DO UPDATE SET
                    size = excluded.size,
                    mtime = excluded.mtime,
                    date_str = excluded.date_str,
                    date_source = excluded.date_source,
                    model = excluded.model,
                    lens = excluded.lens,
                    focal = excluded.focal,
                    media = excluded.media
                """,
                [
                    (scan_key, full, size, mtime,
                     meta["date_str"], meta["date_source"], meta["model"],
                     meta["lens"], meta["focal"], meta["media"])
                    for full, size, mtime, meta in items
                ],
            )
