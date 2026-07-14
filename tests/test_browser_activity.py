from datetime import datetime

import pytest
from fastapi import HTTPException

from src.config import AppConfig, ServerConfig
from src.database import get_db, init_db
from src.routes import browser_activity
from src.routes.browser_activity import BrowserBatch, BrowserSession


def setup_enabled(tmp_path, monkeypatch):
    init_db(str(tmp_path / "tracker.db"))
    with get_db().connect() as conn:
        conn.execute("INSERT INTO sessions(start_time, is_active) VALUES (?, 1)",
                     (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),))
    config = AppConfig(server=ServerConfig(browser_integration=True))
    monkeypatch.setattr(browser_activity, "load_config", lambda: config)


def test_missing_end_uses_start_plus_duration(tmp_path, monkeypatch):
    setup_enabled(tmp_path, monkeypatch)
    start_ms = datetime(2026, 7, 13, 10, 0).timestamp() * 1000
    payload = BrowserSession(
        url="https://github.com", domain="github.com", duration_ms=30_000,
        start_time=start_ms,
    )

    result = browser_activity.receive_browser_activity(payload)

    assert result["data"]["inserted"] == 1
    with get_db().connect() as conn:
        row = conn.execute("SELECT start_time, end_time FROM window_activity").fetchone()
    assert row["start_time"] == "2026-07-13 10:00:00"
    assert row["end_time"] == "2026-07-13 10:00:30"


def test_single_invalid_duration_returns_422(tmp_path, monkeypatch):
    setup_enabled(tmp_path, monkeypatch)
    payload = BrowserSession(url="x", domain="x", duration_ms=-1)

    with pytest.raises(HTTPException) as error:
        browser_activity.receive_browser_activity(payload)

    assert error.value.status_code == 422


def test_batch_reports_invalid_item_and_keeps_valid_item(tmp_path, monkeypatch):
    setup_enabled(tmp_path, monkeypatch)
    payload = BrowserBatch(sessions=[
        BrowserSession(url="https://github.com", domain="github.com", duration_ms=1_000),
        BrowserSession(url="x", domain="x", duration_ms=90_000_000),
    ])

    result = browser_activity.receive_browser_activity(payload)["data"]

    assert result["inserted"] == 1
    assert result["errors"] == [{"index": 1, "reason": "duration_ms must be between 0 and 86400000"}]
