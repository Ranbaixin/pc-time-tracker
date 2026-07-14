"""Shared query layer for normalized activity statistics."""

from __future__ import annotations

from datetime import datetime, timedelta

from .database import get_db
from .intervals import (
    ActivityInterval,
    NormalizedSlice,
    interval_from_row,
    normalize_activity,
    project_browser_details,
)


def day_bounds(date_value: str) -> tuple[datetime, datetime]:
    start = datetime.strptime(date_value, "%Y-%m-%d")
    return start, start + timedelta(days=1)


def load_intervals(
    start: datetime,
    end: datetime,
    *,
    now: datetime | None = None,
    session_id: int | None = None,
) -> list[ActivityInterval]:
    effective_now = now or datetime.now()
    conditions = ["start_time < ?", "COALESCE(end_time, ?) > ?"]
    params: list[object] = [
        end.strftime("%Y-%m-%d %H:%M:%S"),
        effective_now.strftime("%Y-%m-%d %H:%M:%S"),
        start.strftime("%Y-%m-%d %H:%M:%S"),
    ]
    if session_id is not None:
        conditions.append("session_id = ?")
        params.append(session_id)
    db = get_db()
    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM window_activity WHERE {' AND '.join(conditions)} ORDER BY start_time, id",
            params,
        ).fetchall()
    return [item for row in rows if (item := interval_from_row(row)) is not None]


def activity_timeline(
    start: datetime,
    end: datetime,
    *,
    now: datetime | None = None,
    session_id: int | None = None,
) -> list[NormalizedSlice]:
    rows = load_intervals(start, end, now=now, session_id=session_id)
    return normalize_activity(rows, start, end, now=now)


def browser_timeline(
    start: datetime,
    end: datetime,
    *,
    now: datetime | None = None,
) -> list[NormalizedSlice]:
    rows = load_intervals(start, end, now=now)
    desktop = normalize_activity(rows, start, end, now=now)
    return project_browser_details(desktop, rows)


def clip_slice(item: NormalizedSlice, start: datetime, end: datetime) -> tuple[datetime, datetime] | None:
    clipped_start = max(item.start, start)
    clipped_end = min(item.end, end)
    return (clipped_start, clipped_end) if clipped_end > clipped_start else None

