from argparse import Namespace

from src import main
from src.database import get_db, init_db


def test_cli_stats_uses_normalized_totals(tmp_path, monkeypatch, capsys):
    init_db(str(tmp_path / "tracker.db"))
    with get_db().connect() as conn:
        sid = conn.execute(
            "INSERT INTO sessions(start_time, is_active) VALUES (datetime('now','-1 hour'), 0)"
        ).lastrowid
        today = main.datetime.now().strftime("%Y-%m-%d")
        conn.execute(
            """INSERT INTO window_activity(session_id, process_name, start_time, end_time,
               duration_seconds, source) VALUES (?, 'Code.exe', ?, ?, 600, 'desktop')""",
            (sid, f"{today} 10:00:00", f"{today} 10:10:00"),
        )
        conn.execute(
            """INSERT INTO window_activity(session_id, process_name, start_time, end_time,
               duration_seconds, source) VALUES (?, '[System Sleep]', ?, ?, 300, 'system')""",
            (sid, f"{today} 10:05:00", f"{today} 10:10:00"),
        )

    # Avoid cmd_stats reinitializing the global database from the real config.
    monkeypatch.setattr("src.database.init_db", lambda path: get_db())
    main.cmd_stats(Namespace())

    output = capsys.readouterr().out
    assert "Code.exe" in output
    assert "活跃时间: 0h 5min" in output
