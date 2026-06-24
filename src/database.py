"""Database layer — SQLite connection, schema, migrations."""

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Optional


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time      TEXT    NOT NULL,
    end_time        TEXT,
    is_active       INTEGER NOT NULL DEFAULT 1,
    boot_time       TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_sessions_active ON sessions(is_active);
CREATE INDEX IF NOT EXISTS idx_sessions_start  ON sessions(start_time);

CREATE TABLE IF NOT EXISTS window_activity (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id        INTEGER NOT NULL,
    window_title      TEXT    NOT NULL DEFAULT '',
    process_name      TEXT    NOT NULL,
    process_path      TEXT,
    start_time        TEXT    NOT NULL,
    end_time          TEXT,
    duration_seconds  INTEGER,
    tracking_mode     TEXT    NOT NULL DEFAULT 'foreground',
    interaction_count INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_wact_session    ON window_activity(session_id);
CREATE INDEX IF NOT EXISTS idx_wact_process    ON window_activity(process_name);
CREATE INDEX IF NOT EXISTS idx_wact_start      ON window_activity(start_time);
CREATE INDEX IF NOT EXISTS idx_wact_range      ON window_activity(start_time, end_time);
"""


class Database:
    """Thread-safe SQLite connection manager."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._local = threading.local()

    def _get_connection(self) -> sqlite3.Connection:
        """Get or create a thread-local connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return self._local.conn

    @contextmanager
    def connect(self):
        """Context manager yielding a thread-local connection."""
        conn = self._get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def init_schema(self):
        """Create tables and indexes if they don't exist."""
        with self.connect() as conn:
            conn.executescript(SCHEMA_SQL)

    def close(self):
        """Close the thread-local connection if open."""
        if hasattr(self._local, "conn") and self._local.conn is not None:
            self._local.conn.close()
            self._local.conn = None


# Global singleton — initialized at startup
_db: Optional[Database] = None


def init_db(db_path: str) -> Database:
    """Initialize the global database instance."""
    global _db
    _db = Database(db_path)
    _db.init_schema()
    return _db


def get_db() -> Database:
    """Get the global database instance."""
    if _db is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _db
