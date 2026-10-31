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


def test_event_retry_is_idempotent_and_conflicting_payload_rejected(tmp_path, monkeypatch):
    setup_enabled(tmp_path, monkeypatch)
    event = dict(event_id="event-1", url="https://github.com", domain="github.com",
                 duration_ms=1000, start_time=1783908000000)
    first = browser_activity.receive_browser_activity(BrowserBatch(sessions=[event]))["data"]
    retry = browser_activity.receive_browser_activity(BrowserBatch(sessions=[event]))["data"]
    assert first["acknowledgements"][0]["status"] == "inserted"
    assert retry["acknowledgements"][0]["status"] == "duplicate"
    assert retry["inserted"] == 0
    event["duration_ms"] = 2000
    conflict = browser_activity.receive_browser_activity(BrowserBatch(sessions=[event]))["data"]
    assert conflict["acknowledgements"][0]["status"] == "rejected"
    with get_db().connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM window_activity").fetchone()[0] == 1


def test_malformed_batch_item_does_not_block_valid_item(tmp_path, monkeypatch):
    setup_enabled(tmp_path, monkeypatch)
    result = browser_activity.receive_browser_activity(BrowserBatch(sessions=[
        {"event_id": "bad", "duration_ms": "oops"},
        {"event_id": "good", "url": "x", "domain": "x", "duration_ms": 1000},
    ]))["data"]
    assert result["inserted"] == 1
    assert [a["status"] for a in result["acknowledgements"]] == ["rejected", "inserted"]


def test_storage_failure_rolls_back_receipt_and_can_retry(tmp_path, monkeypatch):
    setup_enabled(tmp_path, monkeypatch)
    payload = BrowserBatch(sessions=[dict(event_id="retry", url="x", domain="x", duration_ms=1000)])
    with get_db().connect() as conn:
        conn.execute("CREATE TRIGGER fail_browser BEFORE INSERT ON window_activity BEGIN SELECT RAISE(ABORT, 'disk failure'); END")
    result = browser_activity.receive_browser_activity(payload)["data"]
    assert result["acknowledgements"][0]["status"] == "retry"
    with get_db().connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM browser_receipts").fetchone()[0] == 0
        conn.execute("DROP TRIGGER fail_browser")
    assert browser_activity.receive_browser_activity(payload)["data"]["inserted"] == 1
