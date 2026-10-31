"""Database layer — SQLite connection, schema, migrations."""

import logging
import sqlite3
import threading
from datetime import datetime
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

LATEST_SCHEMA_VERSION = 8

MIGRATION_V2_SQL = """
CREATE INDEX IF NOT EXISTS idx_wact_end ON window_activity(end_time);
CREATE INDEX IF NOT EXISTS idx_wact_source_range
    ON window_activity(source, start_time, end_time);

DROP TRIGGER IF EXISTS trg_one_open_timeline;
CREATE TRIGGER trg_one_open_timeline
BEFORE INSERT ON window_activity
WHEN NEW.end_time IS NULL
 AND COALESCE(NEW.source, 'desktop') IN ('desktop', 'system')
 AND EXISTS (
    SELECT 1 FROM window_activity
    WHERE session_id = NEW.session_id
      AND end_time IS NULL
      AND COALESCE(source, 'desktop') IN ('desktop', 'system')
 )
BEGIN
    SELECT RAISE(ABORT, 'only one open desktop/system activity is allowed per session');
END;
"""


MIGRATION_V7_SQL = """
CREATE TABLE IF NOT EXISTS collection_periods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    last_success_at TEXT NOT NULL,
    observation TEXT NOT NULL CHECK (observation IN ('active', 'idle', 'filtered')),
    tolerance_seconds REAL NOT NULL CHECK (tolerance_seconds > 0),
    interrupted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_collection_range
    ON collection_periods(last_success_at, started_at);
"""


class Database:
    """Thread-safe SQLite connection manager."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._connections: set[sqlite3.Connection] = set()
        self._connections_lock = threading.Lock()

    def _get_connection(self) -> sqlite3.Connection:
        """Create a connection owned by one context-manager scope."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=5.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        with self._connections_lock:
            self._connections.add(conn)
        return conn

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
        finally:
            conn.close()
            with self._connections_lock:
                self._connections.discard(conn)

    def init_schema(self):
        """Create tables and indexes if they don't exist; apply migrations."""
        path = Path(self.db_path)
        existed = path.exists() and path.stat().st_size > 0
        with self.connect() as conn:
            had_legacy_schema = bool(
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='window_activity'"
                ).fetchone()
            )
            conn.executescript(SCHEMA_SQL)
            self._migrate(conn)
            conn.execute(
                """CREATE TABLE IF NOT EXISTS schema_migrations (
                       version INTEGER PRIMARY KEY,
                       applied_at TEXT NOT NULL
                   )"""
            )
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (1, ?)",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),),
            )

        with self.connect() as conn:
            has_v2 = conn.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 2"
            ).fetchone()
        if not has_v2:
            if existed and had_legacy_schema:
                from .backup import backup_database

                backup_database(
                    self.db_path,
                    path.parent / "backups",
                    name_prefix="tracker-pre-migration-v2",
                )
            with self.connect() as conn:
                conn.executescript(MIGRATION_V2_SQL)
                conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (2, ?)",
                    (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),),
                )

        with self.connect() as conn:
            has_v7 = conn.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 7"
            ).fetchone()
        if not has_v7:
            if existed and had_legacy_schema:
                from .backup import backup_database

                backup_database(
                    self.db_path, path.parent / "backups",
                    name_prefix="tracker-pre-migration-v7",
                )
            with self.connect() as conn:
                conn.executescript(MIGRATION_V7_SQL)
                conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (7, ?)",
                    (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),),
                )

        with self.connect() as conn:
            has_v8 = conn.execute("SELECT 1 FROM schema_migrations WHERE version = 8").fetchone()
        if not has_v8:
            if existed and had_legacy_schema:
                from .backup import backup_database
                backup_database(self.db_path, path.parent / "backups",
                                name_prefix="tracker-pre-migration-v8")
            with self.connect() as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS browser_receipts (event_id TEXT PRIMARY KEY, payload_hash TEXT NOT NULL)")
                conn.execute("INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (8, ?)",
                             (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),))

    def schema_version(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
            return int(row[0])

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
        """Close any directly acquired or currently outstanding connections."""
        with self._connections_lock:
            connections = list(self._connections)
            self._connections.clear()
        for conn in connections:
            conn.close()


# Global singleton — initialized at startup
_db: Optional[Database] = None


def init_db(db_path: str) -> Database:
    """Initialize the global database instance."""
    global _db
    if _db is not None:
        _db.close()
    _db = Database(db_path)
    _db.init_schema()
    return _db


def get_db() -> Database:
    """Get the global database instance."""
    if _db is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _db
