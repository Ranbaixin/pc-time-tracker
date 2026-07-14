from src.database import get_db, init_db
from src.routes.stats import get_daily_stats, get_summary, get_timeline
from src.routes.sessions import get_session


def seed(tmp_path):
    init_db(str(tmp_path / "tracker.db"))
    with get_db().connect() as conn:
        session = conn.execute(
            "INSERT INTO sessions(start_time, is_active) VALUES ('2026-07-12 23:50:00', 0)"
        ).lastrowid
        conn.execute(
            """INSERT INTO window_activity(
                session_id, process_name, window_title, start_time, end_time,
                duration_seconds, source, category, sub_category, site_name
            ) VALUES (?, 'chrome.exe', 'GitHub - Chrome', '2026-07-12 23:50:00',
                      '2026-07-13 00:20:00', 1800, 'desktop', '浏览', '浏览器', 'GitHub')""",
            (session,),
        )
        conn.execute(
            """INSERT INTO window_activity(
                session_id, process_name, window_title, start_time, end_time,
                duration_seconds, source
            ) VALUES (?, '[System Sleep]', 'sleep', '2026-07-13 00:10:00',
                      '2026-07-13 00:15:00', 300, 'system')""",
            (session,),
        )
        conn.execute(
            """INSERT INTO window_activity(
                session_id, process_name, window_title, start_time, end_time,
                duration_seconds, source, site_name
            ) VALUES (?, '[Browser] github.com', 'GitHub', '2026-07-13 00:00:00',
                      '2026-07-13 00:10:00', 600, 'browser', 'github.com')""",
            (session,),
        )


def test_summary_clips_cross_day_and_removes_sleep_overlap(tmp_path):
    seed(tmp_path)

    result = get_summary("2026-07-13")["data"]

    assert result["total_seconds"] == 900
    assert result["idle_seconds"] == 300
    assert result["top_processes"][0]["process_name"] == "chrome.exe"
    assert result["top_processes"][0]["total_seconds"] == 900
    assert result["top_processes"][0]["percentage"] == 100.0


def test_daily_allocates_cross_midnight_to_each_day(tmp_path):
    seed(tmp_path)

    result = get_daily_stats(
        days=2, date_from="2026-07-12", date_to="2026-07-13"
    )["data"]["daily"]

    assert result == [
        {"date": "2026-07-12", "total_seconds": 600, "idle_seconds": 0,
         "top_process": "chrome.exe"},
        {"date": "2026-07-13", "total_seconds": 900, "idle_seconds": 300,
         "top_process": "chrome.exe"},
    ]


def test_timeline_never_counts_browser_as_extra_active_time(tmp_path):
    seed(tmp_path)

    timeline = get_timeline("2026-07-13")["data"]["timeline"]

    assert timeline[0]["active_seconds"] == 900
    assert max(x["active_seconds"] for x in timeline) <= 3600


def test_session_breakdown_uses_normalized_timeline(tmp_path):
    seed(tmp_path)

    result = get_session(1)["data"]

    assert result["total_active_seconds"] == 1500
    assert result["activity_breakdown"] == [{
        "process_name": "chrome.exe",
        "window_count": 1,
        "total_seconds": 1500,
        "percentage": 100.0,
    }]
