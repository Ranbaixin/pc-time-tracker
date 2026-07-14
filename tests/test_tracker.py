from pathlib import Path

from src.config import AppConfig
from src.database import get_db, init_db
from src.tracker import TrackerEngine


class FakeWindows:
    title = "one.py - Visual Studio Code"
    process = "Code.exe"
    idle_ticks = 0

    def get_last_input_ticks(self):
        return self.idle_ticks

    def get_foreground_window(self):
        return 1

    def get_window_text(self, hwnd):
        return self.title

    def get_window_process_id(self, hwnd):
        return 10

    def get_process_name(self, pid):
        return self.process

    def get_process_path(self, pid):
        return f"C:/{self.process}"

    def get_boot_time(self):
        return "2026-07-13 08:00:00"


def make_engine(tmp_path: Path, fake_clock):
    init_db(str(tmp_path / "tracker.db"))
    config = AppConfig()
    windows = FakeWindows()
    engine = TrackerEngine(config, clock=fake_clock, windows=windows)
    with get_db().connect() as conn:
        session_id = conn.execute(
            """INSERT INTO sessions(start_time, is_active, boot_time)
               VALUES ('2026-07-13 08:00:00', 1, '2026-07-13 08:00:00')"""
        ).lastrowid
    return engine, windows, session_id


def rows():
    with get_db().connect() as conn:
        return [dict(x) for x in conn.execute("SELECT * FROM window_activity ORDER BY id")]


def test_title_change_closes_and_reclassifies_activity(tmp_path, fake_clock):
    engine, windows, session_id = make_engine(tmp_path, fake_clock)
    engine._poll_once(session_id)
    fake_clock.advance(10)
    windows.title = "two.py - Visual Studio Code"

    engine._poll_once(session_id)

    saved = rows()
    assert len(saved) == 2
    assert saved[0]["end_time"] == "2026-07-13 10:00:10"
    assert saved[0]["window_title"].startswith("one.py")
    assert saved[1]["window_title"].startswith("two.py")


def test_idle_closes_activity_at_last_input_and_opens_system_idle(tmp_path, fake_clock):
    engine, windows, session_id = make_engine(tmp_path, fake_clock)
    engine._poll_once(session_id)
    fake_clock.advance(700)
    windows.idle_ticks = 700_000

    engine._poll_once(session_id)

    saved = rows()
    assert saved[0]["end_time"] == "2026-07-13 10:00:00"
    assert saved[1]["process_name"] == "[System Idle]"
    assert saved[1]["source"] == "system"
    assert saved[1]["start_time"] == "2026-07-13 10:00:00"


def test_sleep_closes_activity_at_last_poll_and_records_only_gap(tmp_path, fake_clock):
    engine, windows, session_id = make_engine(tmp_path, fake_clock)
    engine._poll_once(session_id)
    engine._last_poll_monotonic = fake_clock.monotonic()
    engine._last_poll_wall_time = fake_clock.now()
    fake_clock.advance(120)

    assert engine._detect_sleep(session_id) is True

    saved = rows()
    assert saved[0]["end_time"] == "2026-07-13 10:00:00"
    assert saved[0]["duration_seconds"] == 0
    assert saved[1]["process_name"] == "[System Sleep]"
    assert saved[1]["start_time"] == "2026-07-13 10:00:00"
    assert saved[1]["end_time"] == "2026-07-13 10:02:00"
    assert saved[1]["duration_seconds"] == 120


def test_empty_title_is_skipped_when_configured(tmp_path, fake_clock):
    engine, windows, session_id = make_engine(tmp_path, fake_clock)
    windows.title = ""

    engine._poll_once(session_id)

    assert rows() == []


def test_restart_closes_stale_open_activity_before_starting_new_one(tmp_path, fake_clock):
    engine, windows, session_id = make_engine(tmp_path, fake_clock)
    with get_db().connect() as conn:
        conn.execute(
            """INSERT INTO window_activity(session_id, process_name, window_title,
               start_time, source) VALUES (?, 'old.exe', 'old', '2026-07-13 09:00:00', 'desktop')""",
            (session_id,),
        )

    engine._poll_once(session_id)

    saved = rows()
    assert saved[0]["end_time"] == "2026-07-13 10:00:00"
    assert saved[1]["process_name"] == "Code.exe"
    assert saved[1]["end_time"] is None
