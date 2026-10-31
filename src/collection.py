"""Persist successful desktop polls independently of activity and API timestamps.

Coverage is bounded polling evidence, not proof of user presence or OS sleep.
All wall times use the existing database's machine-local, naive time convention.
"""
from datetime import datetime, timedelta
import logging
import sqlite3

from .database import get_db

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
STALE_AFTER_SECONDS = 86400
logger = logging.getLogger(__name__)


def stamp(value):
    return value.strftime(TIME_FORMAT)


class CollectionRecorder:
    """One instance per engine lifetime; never join coverage across restarts."""

    def __init__(self, clock):
        self.clock = clock
        self.period_id = None
        self.last_time = None
        self.last_monotonic = None
        self.last_session = None
        self.last_tolerance = None

    def interrupt(self):
        # Keep the persisted last success unchanged on failure/shutdown.
        period_id, self.period_id = self.period_id, None
        if period_id is not None:
            try:
                with get_db().connect() as conn:
                    conn.execute(
                        "UPDATE collection_periods SET interrupted = 1 WHERE id = ?",
                        (period_id,),
                    )
            except sqlite3.Error:
                # Disk failure must not terminate the retry loop or join periods
                # on recovery. The persisted timestamp still expires normally.
                logger.exception("Could not persist collection interruption")

    def record(self, now, session_id, observation, tolerance):
        monotonic = self.clock.monotonic()
        continuous = (
            self.period_id is not None
            and self.last_session == session_id
            and self.last_tolerance == tolerance
            and 0 <= (now - self.last_time).total_seconds() <= tolerance
            and 0 <= monotonic - self.last_monotonic <= tolerance
        )
        with get_db().connect() as conn:
            if continuous:
                conn.execute(
                    """UPDATE collection_periods SET last_success_at = ?, observation = ?
                       WHERE id = ?""", (stamp(now), observation, self.period_id),
                )
                period_id = self.period_id
            else:
                period_id = conn.execute(
                    """INSERT INTO collection_periods
                       (session_id, started_at, last_success_at, observation, tolerance_seconds)
                       VALUES (?, ?, ?, ?, ?)""",
                    (session_id, stamp(now), stamp(now), observation, tolerance),
                ).lastrowid
        # Advance in-memory state only after commit succeeds.
        self.period_id = period_id
        self.last_time = now
        self.last_monotonic = monotonic
        self.last_session = session_id
        self.last_tolerance = tolerance


def _coverage(rows, start, end):
    intervals = []
    for row in rows:
        left = max(start, datetime.strptime(row["started_at"], TIME_FORMAT))
        right = min(end, datetime.strptime(row["last_success_at"], TIME_FORMAT))
        if right <= left:
            continue
        if intervals and left <= intervals[-1][1]:
            intervals[-1] = (intervals[-1][0], max(right, intervals[-1][1]))
        else:
            intervals.append((left, right))
    unknown = []
    cursor = start
    for left, right in intervals:
        if left > cursor:
            unknown.append({"start": stamp(cursor), "end": stamp(left)})
        cursor = right
    if cursor < end:
        unknown.append({"start": stamp(cursor), "end": stamp(end)})
    covered = sum((right - left).total_seconds() for left, right in intervals)
    return {
        "start": stamp(start), "end": stamp(end),
        "status": "unknown" if not intervals else ("partial" if unknown else "complete"),
        "covered_seconds": int(covered),
        "observed_intervals": [{"start": stamp(a), "end": stamp(b)} for a, b in intervals],
        "unknown_intervals": unknown,
    }


def get_collection_quality(now=None):
    """Read persisted evidence; an API read must never refresh collection time."""
    now = (now or datetime.now()).replace(microsecond=0)
    today = now.replace(hour=0, minute=0, second=0)
    week = today - timedelta(days=6)
    with get_db().connect() as conn:
        conn.execute("BEGIN")
        latest = conn.execute("SELECT * FROM collection_periods ORDER BY id DESC LIMIT 1").fetchone()
        rows = conn.execute(
            """SELECT * FROM collection_periods WHERE last_success_at >= ?
               AND started_at <= ? ORDER BY started_at, id""", (stamp(week), stamp(now)),
        ).fetchall()
        # Historical activity bounds are informational, never collection evidence.
        bounds = conn.execute(
            """SELECT MIN(start_time), MAX(COALESCE(end_time, start_time))
               FROM window_activity WHERE COALESCE(source, 'desktop') IN ('desktop', 'system')"""
        ).fetchone()
    age = None
    freshness = "unknown"
    status = "coverage_unknown"
    if latest:
        age = (now - datetime.strptime(latest["last_success_at"], TIME_FORMAT)).total_seconds()
        if age < 0:
            freshness = "unknown"
            status = "coverage_unknown"
        elif age > STALE_AFTER_SECONDS:
            freshness = "stale"
            status = "historical_stale"
        elif latest["interrupted"] or age > latest["tolerance_seconds"]:
            freshness = "expired"
            status = "interrupted"
        else:
            freshness = "fresh"
            status = {"active": "collecting", "idle": "idle", "filtered": "no_activity"}[
                latest["observation"]
            ]
    return {
        "source": "desktop_successful_poll",
        "time_basis": "machine_local_naive",
        "last_successful_collection_at": latest["last_success_at"] if latest else None,
        "last_observation": latest["observation"] if latest else None,
        "last_session_id": latest["session_id"] if latest else None,
        "age_seconds": age,
        "freshness": freshness,
        "collection_status": status,
        "freshness_threshold_seconds": latest["tolerance_seconds"] if latest else None,
        "stale_after_seconds": STALE_AFTER_SECONDS,
        "activity_record_bounds": {"oldest": bounds[0], "latest": bounds[1]},
        "coverage": {"today": _coverage(rows, today, now), "last_7_days": _coverage(rows, week, now)},
    }
