"""Database layer — SQLite connection, schema, migrations."""

import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def safe_add_column(conn: sqlite3.Connection, table: str, column: str, col_type: str):
    """Add column only if it doesn't exist (SQLite has no IF NOT EXISTS for ALTER).

    Uses PRAGMA table_info() to check first; try/except as race-condition guard.
    """
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            logger.debug(f"Migration: added {table}.{column} {col_type}")
        except sqlite3.OperationalError:
            pass  # Race condition — another thread added it first


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
        """Create tables and indexes if they don't exist; apply migrations."""
        with self.connect() as conn:
            conn.executescript(SCHEMA_SQL)
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection):
        """Apply column and table migrations safely (no IF NOT EXISTS in SQLite)."""
        # window_activity new columns (C2)
        new_cols = [
            ("category",      "TEXT DEFAULT ''"),
            ("sub_category",  "TEXT DEFAULT ''"),
            ("site_name",     "TEXT DEFAULT ''"),
            ("project_name",  "TEXT DEFAULT ''"),
            ("file_type",     "TEXT DEFAULT ''"),
            ("content_type",  "TEXT DEFAULT ''"),
            ("keywords",      "TEXT DEFAULT ''"),
            ("source",        "TEXT DEFAULT 'desktop'"),
            ("mem_peak_mb",   "INTEGER DEFAULT 0"),
            ("is_fullscreen", "INTEGER DEFAULT 0"),
            ("battery_pct",   "INTEGER DEFAULT -1"),
            ("power_plugged", "INTEGER DEFAULT 0"),
        ]
        for col_name, col_type in new_cols:
            safe_add_column(conn, "window_activity", col_name, col_type)

        # app_categories table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_categories (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                process_match  TEXT NOT NULL UNIQUE,
                category       TEXT NOT NULL,
                sub_category   TEXT,
                is_site        INTEGER DEFAULT 0,
                rule           TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS resource_samples (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                activity_id    INTEGER NOT NULL,
                session_id     INTEGER NOT NULL,
                timestamp      TEXT    NOT NULL,
                cpu_percent    REAL,
                ram_used_mb    INTEGER,
                ram_percent    REAL,
                battery_pct    INTEGER,
                power_plugged  INTEGER,
                FOREIGN KEY (activity_id) REFERENCES window_activity(id) ON DELETE CASCADE,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )
        """)

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
