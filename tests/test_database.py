import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src.backup import _rotate_backups, backup_database, list_backups
from src.database import Database, init_db


def test_online_backup_is_integral_and_preserves_rows(tmp_path: Path):
    source = tmp_path / "tracker.db"
    conn = sqlite3.connect(source)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO sample(value) VALUES ('kept')")
    conn.commit()

    backup = backup_database(str(source), tmp_path / "backups", keep=2)

    copied = sqlite3.connect(backup)
    assert copied.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert copied.execute("SELECT value FROM sample").fetchone()[0] == "kept"
    copied.close()
    conn.close()


def test_backup_missing_source_and_rotation(tmp_path: Path):
    assert backup_database(str(tmp_path / "missing.db")) is None
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    for name in ("tracker-2026-01-01-000000.db", "tracker-2026-01-02-000000.db",
                 "tracker-2026-01-03-000000.db"):
        (backup_dir / name).write_bytes(b"x")

    _rotate_backups(backup_dir, keep=2)

    assert len(list_backups(str(backup_dir))) == 2


def test_backup_uses_default_directory_and_list_handles_missing(tmp_path: Path, monkeypatch):
    source = tmp_path / "tracker.db"
    sqlite3.connect(source).close()

    path = backup_database(str(source))

    assert Path(path).parent == tmp_path / "backups"
    monkeypatch.chdir(tmp_path)
    assert list_backups("does-not-exist") == []


def test_failed_online_backup_removes_partial_file(tmp_path: Path, monkeypatch):
    source = tmp_path / "tracker.db"
    source.write_bytes(b"source")

    class Connection:
        def backup(self, destination):
            raise sqlite3.DatabaseError("copy failed")

        def close(self):
            return None

    monkeypatch.setattr("src.backup.sqlite3.connect", lambda path, **kwargs: Connection())

    with pytest.raises(sqlite3.DatabaseError, match="copy failed"):
        backup_database(str(source), tmp_path / "backups")
    assert list((tmp_path / "backups").glob("*.db")) == []


def test_schema_migrations_are_idempotent_and_non_destructive(tmp_path: Path):
    path = tmp_path / "tracker.db"
    db = Database(str(path))
    db.init_schema()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO sessions(start_time, is_active) VALUES ('2026-07-13 10:00:00', 1)"
        )
        conn.execute(
            """INSERT INTO window_activity(
                session_id, process_name, start_time, end_time, duration_seconds
            ) VALUES (1, 'Code.exe', '2026-07-13 10:00:00', '2026-07-13 10:01:00', 60)"""
        )
    db.init_schema()

    with db.connect() as conn:
        assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 8
        assert conn.execute("SELECT COUNT(*) FROM window_activity").fetchone()[0] == 1


def test_schema_rejects_two_open_timeline_rows(tmp_path: Path):
    db = Database(str(tmp_path / "tracker.db"))
    db.init_schema()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO sessions(start_time, is_active) VALUES ('2026-07-13 10:00:00', 1)"
        )
        conn.execute(
            """INSERT INTO window_activity(session_id, process_name, start_time, source)
            VALUES (1, 'Code.exe', '2026-07-13 10:00:00', 'desktop')"""
        )

    with pytest.raises(sqlite3.IntegrityError):
        with db.connect() as conn:
            conn.execute(
                """INSERT INTO window_activity(session_id, process_name, start_time, source)
                VALUES (1, '[System Idle]', '2026-07-13 10:01:00', 'system')"""
            )


def test_pending_migration_creates_pre_migration_backup(tmp_path: Path):
    path = tmp_path / "tracker.db"
    db = Database(str(path))
    db.init_schema()
    with db.connect() as conn:
        conn.execute("DELETE FROM schema_migrations WHERE version = 2")
        conn.execute("DROP TRIGGER IF EXISTS trg_one_open_timeline")
    db.close()

    Database(str(path)).init_schema()

    backups = list((tmp_path / "backups").glob("tracker-pre-migration-v2-*.db"))
    assert len(backups) == 1


def test_reinitializing_global_database_closes_previous_connection(tmp_path: Path):
    old = init_db(str(tmp_path / "old.db"))
    old_connection = old._get_connection()

    init_db(str(tmp_path / "new.db"))

    with pytest.raises(sqlite3.ProgrammingError):
        old_connection.execute("SELECT 1")


def test_connect_closes_worker_thread_connection_after_context(tmp_path: Path):
    db = Database(str(tmp_path / "worker.db"))
    db.init_schema()

    def use_connection():
        with db.connect() as conn:
            conn.execute("SELECT 1")
            return conn

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        connection = pool.submit(use_connection).result()
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")
    finally:
        pool.shutdown()
