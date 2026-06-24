"""Statistics endpoints — /api/v1/stats/*"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Query

from ..database import get_db

router = APIRouter(prefix="/stats", tags=["Statistics"])


def _resolve_date(date_str: str) -> str:
    """Resolve 'today' to actual ISO date."""
    if date_str and date_str.lower() == "today":
        return datetime.now().strftime("%Y-%m-%d")
    return date_str or datetime.now().strftime("%Y-%m-%d")


@router.get("/summary")
def get_summary(
    date: str = Query(default="today", description="Date in YYYY-MM-DD or 'today'"),
):
    """Get a single-day summary: active time, idle time, top processes."""
    date = _resolve_date(date)
    db = get_db()

    with db.connect() as conn:
        # Total active time (non-sleep, non-idle windows)
        row = conn.execute(
            """SELECT COALESCE(SUM(duration_seconds), 0) as total_seconds,
                      COUNT(*) as window_count
               FROM window_activity
               WHERE process_name != '[System Sleep]'
                 AND date(start_time) = ?
                 AND duration_seconds IS NOT NULL""",
            (date,),
        ).fetchone()

        total_seconds = int(row["total_seconds"])
        window_count = row["window_count"]

        # Idle / sleep time
        idle_row = conn.execute(
            """SELECT COALESCE(SUM(duration_seconds), 0) as idle_seconds
               FROM window_activity
               WHERE (process_name = '[System Sleep]' OR window_title LIKE '%sleep%')
                 AND date(start_time) = ?
                 AND duration_seconds IS NOT NULL""",
            (date,),
        ).fetchone()
        idle_seconds = int(idle_row["idle_seconds"])

        # Top processes
        top = conn.execute(
            """SELECT process_name,
                      COUNT(*) as window_count,
                      COALESCE(SUM(duration_seconds), 0) as total_seconds
               FROM window_activity
               WHERE process_name NOT IN ('[System Sleep]', '<exited>', '[Protected]')
                 AND date(start_time) = ?
                 AND duration_seconds IS NOT NULL
               GROUP BY process_name
               ORDER BY total_seconds DESC
               LIMIT 10""",
            (date,),
        ).fetchall()

    top_processes = []
    for t in top:
        pct = round((t["total_seconds"] / total_seconds * 100) if total_seconds > 0 else 0, 1)
        top_processes.append({
            "process_name": t["process_name"],
            "total_seconds": int(t["total_seconds"]),
            "percentage": pct,
            "window_count": t["window_count"],
        })

    return {
        "data": {
            "date": date,
            "total_seconds": total_seconds,
            "idle_seconds": idle_seconds,
            "window_count": window_count,
            "top_processes": top_processes,
        }
    }


@router.get("/daily")
def get_daily_stats(
    days: int = Query(default=7, ge=1, le=90),
    date_from: str = Query(default=None),
    date_to: str = Query(default=None),
):
    """Get multi-day breakdown of usage."""
    if date_from and date_to:
        pass
    else:
        date_to = datetime.now().strftime("%Y-%m-%d")
        date_from = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")

    db = get_db()
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT date(start_time) as date,
                      COALESCE(SUM(CASE WHEN process_name != '[System Sleep]'
                                        THEN duration_seconds ELSE 0 END), 0) as total_seconds,
                      COALESCE(SUM(CASE WHEN process_name = '[System Sleep]'
                                        THEN duration_seconds ELSE 0 END), 0) as idle_seconds
               FROM window_activity
               WHERE date(start_time) BETWEEN ? AND ?
                 AND duration_seconds IS NOT NULL
               GROUP BY date(start_time)
               ORDER BY date ASC""",
            (date_from, date_to),
        ).fetchall()

    daily_data = []
    for r in rows:
        # Get top process for this day
        top = conn.execute(
            """SELECT process_name, SUM(duration_seconds) as s
               FROM window_activity
               WHERE date(start_time) = ?
                 AND process_name NOT IN ('[System Sleep]', '<exited>')
                 AND duration_seconds IS NOT NULL
               GROUP BY process_name
               ORDER BY s DESC LIMIT 1""",
            (r["date"],),
        ).fetchone()

        daily_data.append({
            "date": r["date"],
            "total_seconds": int(r["total_seconds"]),
            "idle_seconds": int(r["idle_seconds"]),
            "top_process": top["process_name"] if top else None,
        })

    return {"data": {"daily": daily_data, "date_from": date_from, "date_to": date_to}}


@router.get("/processes")
def get_top_processes(
    date: str = Query(default="today"),
    date_from: str = Query(default=None),
    date_to: str = Query(default=None),
    limit: int = Query(default=10, ge=1, le=50),
):
    """Get top-N processes by usage time."""
    if date_from and date_to:
        pass
    else:
        date = _resolve_date(date)
        date_from = date
        date_to = date

    db = get_db()
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT process_name,
                      COUNT(*) as window_count,
                      COALESCE(SUM(duration_seconds), 0) as total_seconds
               FROM window_activity
               WHERE date(start_time) BETWEEN ? AND ?
                 AND process_name NOT IN ('[System Sleep]', '<exited>', '[Protected]')
                 AND duration_seconds IS NOT NULL
               GROUP BY process_name
               ORDER BY total_seconds DESC
               LIMIT ?""",
            (date_from, date_to, limit),
        ).fetchall()

    total = sum(r["total_seconds"] for r in rows)

    return {
        "data": {
            "processes": [
                {
                    "process_name": r["process_name"],
                    "total_seconds": int(r["total_seconds"]),
                    "percentage": round(
                        (r["total_seconds"] / total * 100) if total > 0 else 0, 1
                    ),
                    "window_count": r["window_count"],
                }
                for r in rows
            ],
            "date_from": date_from,
            "date_to": date_to,
        }
    }


@router.get("/timeline")
def get_timeline(
    date: str = Query(default="today"),
):
    """Get hourly breakdown (24-element array) for a date."""
    date = _resolve_date(date)
    db = get_db()

    with db.connect() as conn:
        # Raw hourly aggregation
        rows = conn.execute(
            """SELECT CAST(strftime('%H', start_time) AS INTEGER) as hour,
                      COALESCE(SUM(duration_seconds), 0) as active_seconds
               FROM window_activity
               WHERE date(start_time) = ?
                 AND process_name != '[System Sleep]'
                 AND duration_seconds IS NOT NULL
               GROUP BY hour
               ORDER BY hour""",
            (date,),
        ).fetchall()

    hour_map = {r["hour"]: int(r["active_seconds"]) for r in rows}

    timeline = []
    for h in range(24):
        seconds = hour_map.get(h, 0)

        # Get top process for this hour
        top = conn.execute(
            """SELECT process_name, SUM(duration_seconds) as s
               FROM window_activity
               WHERE date(start_time) = ?
                 AND CAST(strftime('%H', start_time) AS INTEGER) = ?
                 AND process_name NOT IN ('[System Sleep]', '<exited>')
                 AND duration_seconds IS NOT NULL
               GROUP BY process_name
               ORDER BY s DESC LIMIT 1""",
            (date, h),
        ).fetchone()

        timeline.append({
            "hour": h,
            "active_seconds": seconds,
            "top_process": top["process_name"] if top else None,
        })

    return {"data": {"date": date, "timeline": timeline}}
