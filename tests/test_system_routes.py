from pathlib import Path

import pytest
from fastapi import HTTPException

from src.config import AppConfig
from src.database import get_db, init_db
from src.routes import sessions, system


class FakeAutostart:
    def __init__(self, fail=False):
        self.enabled = False
        self.fail = fail

    def is_enabled(self):
        return self.enabled

    def enable(self):
        if self.fail:
            raise OSError("denied")
        self.enabled = True
        return Path("launcher.vbs")

    def disable(self):
        if self.fail:
            raise OSError("denied")
        self.enabled = False


def test_autostart_and_browser_toggle_routes(tmp_path, monkeypatch):
    init_db(str(tmp_path / "tracker.db"))
    service = FakeAutostart()
    monkeypatch.setattr("src.autostart.get_autostart_service", lambda: service)
    config = AppConfig()
    saved = []
    monkeypatch.setattr(system, "load_config", lambda: config)
    monkeypatch.setattr("src.config.load_config", lambda: config)
    monkeypatch.setattr("src.config.save_config", saved.append)

    assert system.get_autostart()["data"]["enabled"] is False
    assert system.set_autostart(system.AutostartRequest(enabled=True))["data"]["enabled"] is True
    assert system.get_autostart()["data"]["enabled"] is True
    assert system.set_autostart(system.AutostartRequest(enabled=False))["data"]["enabled"] is False
    assert system.get_browser_integration()["data"]["enabled"] is False
    result = system.set_browser_integration(system.AutostartRequest(enabled=True))
    assert result["data"]["enabled"] is True
    assert saved[0].server.browser_integration is True


def test_autostart_failure_is_non_success(tmp_path, monkeypatch):
    init_db(str(tmp_path / "tracker.db"))
    monkeypatch.setattr("src.autostart.get_autostart_service", lambda: FakeAutostart(True))

    with pytest.raises(HTTPException) as error:
        system.set_autostart(system.AutostartRequest(enabled=True))

    assert error.value.status_code == 500


def test_backup_routes_and_force_end_session(tmp_path, monkeypatch):
    init_db(str(tmp_path / "tracker.db"))
    with get_db().connect() as conn:
        sid = conn.execute(
            "INSERT INTO sessions(start_time, is_active) VALUES ('2026-07-14 10:00:00', 1)"
        ).lastrowid
        conn.execute(
            """INSERT INTO window_activity(session_id, process_name, start_time, source)
               VALUES (?, 'Code.exe', '2026-07-14 10:00:00', 'desktop')""",
            (sid,),
        )
    config = AppConfig()
    config.database.path = str(tmp_path / "tracker.db")
    monkeypatch.setattr("src.config.load_config", lambda: config)
    monkeypatch.setattr("src.backup.backup_database", lambda *args, **kwargs: "backup.db")
    monkeypatch.setattr("src.backup.list_backups", lambda: [{"name": "backup.db"}])

    assert system.create_backup()["data"]["path"] == "backup.db"
    assert system.list_backups()["data"]["count"] == 1
    assert sessions.force_end_current_session()["data"]["closed"] is True
    assert sessions.force_end_current_session()["error"] == "No active session to end"
