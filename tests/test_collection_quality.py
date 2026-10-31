from datetime import timedelta
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.collection import CollectionRecorder, get_collection_quality
from src.database import Database, get_db, init_db
from src.routes import agent
from src.tracker import TrackerEngine
from test_tracker import make_engine


def quality(clock):
    return get_collection_quality(clock.now())


def test_success_is_persisted_even_without_window_changes(tmp_path, fake_clock):
    engine, _, sid = make_engine(tmp_path, fake_clock)
    engine._poll_once(sid)
    fake_clock.advance(5)
    engine._poll_once(sid)
    with get_db().connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM window_activity').fetchone()[0] == 1
    # Re-open DB: API-only processes can see collection evidence.
    init_db(str(tmp_path / 'tracker.db'))
    result = quality(fake_clock)
    assert result['last_successful_collection_at'] == '2026-07-13 10:00:05'
    assert result['collection_status'] == 'collecting'
    assert result['coverage']['today']['covered_seconds'] == 5
    assert result['coverage']['last_7_days']['status'] == 'partial'


def test_interruption_and_recovery_never_bridge_missing_polls(tmp_path, fake_clock):
    engine, windows, sid = make_engine(tmp_path, fake_clock)
    engine._poll_once(sid)
    fake_clock.advance(5)
    engine._poll_once(sid)
    last = quality(fake_clock)['last_successful_collection_at']
    fake_clock.advance(120)
    assert quality(fake_clock)['collection_status'] == 'interrupted'
    assert quality(fake_clock)['last_successful_collection_at'] == last
    # Even if old sleep inference writes a system row, it is not coverage.
    engine._detect_sleep(sid)
    assert quality(fake_clock)['last_successful_collection_at'] == last
    engine._poll_once(sid)
    fake_clock.advance(5)
    engine._poll_once(sid)
    result = quality(fake_clock)
    assert result['collection_status'] == 'collecting'
    assert result['coverage']['today']['covered_seconds'] == 10
    assert {'start': '2026-07-13 10:00:05', 'end': '2026-07-13 10:02:05'} in (
        result['coverage']['today']['unknown_intervals']
    )
    # A process restart also splits evidence, even within the normal tolerance.
    replacement = TrackerEngine(engine.config, clock=fake_clock, windows=windows)
    fake_clock.advance(1)
    replacement._poll_once(sid)
    with get_db().connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM collection_periods').fetchone()[0] == 3


@pytest.mark.parametrize(('idle', 'title', 'expected'), [
    (700_000, 'work', 'idle'), (0, '', 'no_activity'),
])
def test_healthy_no_activity_is_distinct_from_missing_data(
    tmp_path, fake_clock, idle, title, expected,
):
    engine, windows, sid = make_engine(tmp_path, fake_clock)
    windows.idle_ticks = idle
    windows.title = title
    engine._poll_once(sid)
    assert quality(fake_clock)['collection_status'] == expected
    assert quality(fake_clock)['freshness'] == 'fresh'


def test_empty_and_legacy_records_have_unknown_coverage(tmp_path, fake_clock):
    init_db(str(tmp_path / 'tracker.db'))
    assert quality(fake_clock)['collection_status'] == 'coverage_unknown'
    assert quality(fake_clock)['last_successful_collection_at'] is None
    with get_db().connect() as conn:
        sid = conn.execute('INSERT INTO sessions(start_time) VALUES (?)',
                           ('2020-01-01 00:00:00',)).lastrowid
        conn.execute('''INSERT INTO window_activity(session_id, process_name, start_time)
                        VALUES (?, 'stale.exe', '2020-01-01 00:00:00')''', (sid,))
    result = quality(fake_clock)
    assert result['last_successful_collection_at'] is None
    assert result['coverage']['today']['status'] == 'unknown'
    assert result['activity_record_bounds']['latest'] == '2020-01-01 00:00:00'


def test_stale_samples_and_clock_rollback_are_not_fresh(tmp_path, fake_clock):
    engine, _, sid = make_engine(tmp_path, fake_clock)
    engine._poll_once(sid)
    fake_clock.advance(86401)
    assert quality(fake_clock)['collection_status'] == 'historical_stale'
    fake_clock.advance(-86402)
    assert quality(fake_clock)['freshness'] == 'unknown'
    engine._poll_once(sid)
    with get_db().connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM collection_periods').fetchone()[0] == 2


def test_failed_poll_never_refreshes_success_and_loop_invalidates_period(tmp_path, fake_clock):
    engine, windows, sid = make_engine(tmp_path, fake_clock)
    engine._poll_once(sid)
    last = quality(fake_clock)['last_successful_collection_at']
    fake_clock.advance(1)

    def fail():
        engine._stop_event.set()
        raise RuntimeError('Windows API failed')

    windows.get_last_input_ticks = fail
    engine.session_mgr.ensure_session = lambda: {'id': sid}
    # Exercise the actual exception handler; avoid shutdown's historical activity updates here.
    engine._shutdown = lambda: None
    engine._run()
    assert quality(fake_clock)['last_successful_collection_at'] == last
    assert quality(fake_clock)['collection_status'] == 'interrupted'


def test_failed_heartbeat_commit_does_not_advance_memory(tmp_path, fake_clock, monkeypatch):
    engine, _, sid = make_engine(tmp_path, fake_clock)
    recorder = CollectionRecorder(fake_clock)
    with monkeypatch.context() as patch:
        patch.setattr('src.collection.get_db', lambda: (_ for _ in ()).throw(
            sqlite3.OperationalError('disk full')))
        with pytest.raises(sqlite3.OperationalError):
            recorder.record(fake_clock.now(), sid, 'active', 15)
    assert recorder.last_time is None
    assert quality(fake_clock)['last_successful_collection_at'] is None


def test_context_separates_generated_time_and_hides_stale_window(tmp_path, fake_clock, monkeypatch):
    engine, _, sid = make_engine(tmp_path, fake_clock)
    engine._poll_once(sid)

    class ClockDateTime:
        @staticmethod
        def now():
            return fake_clock.now()

    monkeypatch.setattr(agent, 'datetime', ClockDateTime)
    monkeypatch.setattr(agent, 'load_config', lambda: engine.config)
    app = FastAPI()
    app.include_router(agent.router, prefix='/api/v1')
    client = TestClient(app)
    before = client.get('/api/v1/agent/context').json()['data']
    assert before['_metadata']['current_session_is_live'] is True
    fake_clock.advance(120)
    with get_db().connect() as conn:
        original = [tuple(r) for r in conn.execute('SELECT * FROM window_activity')]
    result = client.get('/api/v1/agent/context').json()['data']
    assert result['_metadata']['generated_at'] == '2026-07-13 10:02:00'
    assert result['_metadata']['collection_quality']['last_successful_collection_at'] == (
        '2026-07-13 10:00:00'
    )
    assert result['current_session']['active_window'] is None
    assert result['_metadata']['current_session_is_live'] is False
    assert '未覆盖区间不能解释为休息' in result['text_summary']
    assert result['today_summary'] == before['today_summary']  # existing statistical semantics
    with get_db().connect() as conn:
        assert [tuple(r) for r in conn.execute('SELECT * FROM window_activity')] == original


def test_v2_migration_preserves_history_and_does_not_backfill_samples(tmp_path):
    path = tmp_path / 'tracker.db'
    db = Database(str(path))
    db.init_schema()
    with db.connect() as conn:
        conn.execute("INSERT INTO sessions(start_time) VALUES ('2020-01-01 00:00:00')")
        conn.execute('''INSERT INTO window_activity(session_id, process_name, start_time)
                        VALUES (1, 'old.exe', '2020-01-01 00:00:00')''')
        original = tuple(conn.execute('SELECT * FROM window_activity').fetchone())
        conn.execute('DROP TABLE collection_periods')
        conn.execute('DELETE FROM schema_migrations WHERE version = 7')
    db.init_schema()
    db.init_schema()
    assert db.schema_version() == 8
    with db.connect() as conn:
        assert tuple(conn.execute('SELECT * FROM window_activity').fetchone()) == original
        assert conn.execute('SELECT COUNT(*) FROM collection_periods').fetchone()[0] == 0
    assert len(list((tmp_path / 'backups').glob('tracker-pre-migration-v7-*.db'))) == 1


def test_clean_stop_is_not_a_successful_poll(tmp_path, fake_clock):
    engine, _, sid = make_engine(tmp_path, fake_clock)
    engine._poll_once(sid)
    fake_clock.advance(1)
    engine.collection.interrupt()
    assert quality(fake_clock)['collection_status'] == 'interrupted'
    assert quality(fake_clock)['last_successful_collection_at'] == '2026-07-13 10:00:00'


def test_coverage_clips_midnight_and_merges_contiguous_periods(tmp_path, fake_clock):
    init_db(str(tmp_path / 'tracker.db'))
    fake_clock.current = fake_clock.now().replace(hour=0, minute=0, second=5)
    recorder = CollectionRecorder(fake_clock)
    first = fake_clock.now() - timedelta(seconds=10)
    fake_clock.current = first
    recorder.record(first, 1, 'idle', 15)
    fake_clock.advance(10)
    recorder.record(fake_clock.now(), 1, 'idle', 15)
    result = quality(fake_clock)
    assert result['coverage']['today']['covered_seconds'] == 5
    assert result['coverage']['today']['status'] == 'complete'
    fake_clock.advance(1)
    assert quality(fake_clock)['coverage']['today']['status'] == 'partial'


def test_interruption_write_failure_still_splits_recovery(tmp_path, fake_clock, monkeypatch):
    engine, _, sid = make_engine(tmp_path, fake_clock)
    engine._poll_once(sid)
    with monkeypatch.context() as patch:
        patch.setattr('src.collection.get_db', lambda: (_ for _ in ()).throw(
            sqlite3.OperationalError('disk unavailable')))
        engine.collection.interrupt()
    assert engine.collection.period_id is None
    fake_clock.advance(1)
    engine._poll_once(sid)
    with get_db().connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM collection_periods').fetchone()[0] == 2


def test_adjacent_periods_merge_only_for_coverage_reporting(tmp_path, fake_clock):
    engine, _, sid = make_engine(tmp_path, fake_clock)
    engine._poll_once(sid)
    fake_clock.advance(5)
    engine._poll_once(sid)
    engine.collection.interrupt()
    engine._poll_once(sid)
    fake_clock.advance(5)
    engine._poll_once(sid)
    result = quality(fake_clock)
    assert result['coverage']['today']['covered_seconds'] == 10
    assert len(result['coverage']['today']['observed_intervals']) == 1

