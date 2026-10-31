from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config import AppConfig
from src.database import get_db, init_db
from src.routes import activity, agent, classifications, sessions, stats, system


def make_client(tmp_path, monkeypatch):
    init_db(str(tmp_path / "tracker.db"))
    config = AppConfig()
    config.database.path = str(tmp_path / "tracker.db")
    config.logging.file = str(tmp_path / "tracker.log")
    monkeypatch.setattr(agent, "load_config", lambda: config)
    monkeypatch.setattr(system, "load_config", lambda: config)
    now = datetime.now().replace(microsecond=0)
    start = now - timedelta(minutes=20)
    with get_db().connect() as conn:
        sid = conn.execute(
            "INSERT INTO sessions(start_time, is_active) VALUES (?, 1)",
            (start.strftime("%Y-%m-%d %H:%M:%S"),),
        ).lastrowid
        conn.execute(
            """INSERT INTO window_activity(
               session_id, process_name, window_title, start_time, end_time,
               duration_seconds, source, category, sub_category, site_name, keywords)
               VALUES (?, 'Code.exe', 'work.py - Visual Studio Code', ?, ?, 600,
                       'desktop', '开发', 'IDE', '', 'work,python')""",
            (sid, start.strftime("%Y-%m-%d %H:%M:%S"),
             (start + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")),
        )
    app = FastAPI()
    for route in (sessions.router, activity.router, stats.router, agent.router,
                  classifications.router, system.router, system.backup_router,
                  system.browser_router):
        app.include_router(route, prefix="/api/v1")
    return TestClient(app)


def test_read_api_contracts_share_normalized_totals(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    summary = client.get("/api/v1/stats/summary").json()["data"]
    context = client.get("/api/v1/agent/context").json()["data"]

    assert summary["total_seconds"] == 600
    assert context["today_summary"]["total_seconds"] == 600
    assert len(client.get("/api/v1/stats/timeline").json()["data"]["timeline"]) == 24
    assert client.get("/api/v1/stats/categories").status_code == 200
    assert client.get("/api/v1/stats/sites").status_code == 200
    assert client.get("/api/v1/stats/content_types").status_code == 200
    assert client.get("/api/v1/stats/file_types").status_code == 200
    assert client.get("/api/v1/stats/keywords?days=1").status_code == 200


def test_session_activity_agent_and_status_contracts(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    assert client.get("/api/v1/sessions").json()["data"]["total"] == 1
    assert client.get("/api/v1/sessions/current").status_code == 200
    assert client.get("/api/v1/sessions/1").status_code == 200
    assert client.get("/api/v1/activity").json()["data"]["total"] == 1
    assert client.get("/api/v1/activity/recent").status_code == 200
    assert client.get("/api/v1/agent/raw?days=1").status_code == 200
    assert client.get("/api/v1/agent/insights").status_code == 200
    prompt = client.get("/api/v1/agent/prompt").json()["data"]
    assert prompt["scenario"] == "daily_review"
    status = client.get("/api/v1/status").json()["data"]
    assert status["schema_version"] == 4


def test_classification_rule_lifecycle(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    created = client.post("/api/v1/classifications", json={
        "process_match": "custom.exe", "category": "开发", "sub_category": "工具"
    })
    assert created.status_code == 200
    rules = client.get("/api/v1/classifications").json()["data"]["rules"]
    assert len(rules) == 1
    rule_id = rules[0]["id"]
    assert client.delete(f"/api/v1/classifications/{rule_id}").status_code == 200
