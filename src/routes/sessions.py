"""Session endpoints — /api/v1/sessions/*"""

import time
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from ..database import get_db
from ..models import CurrentSession, ActiveWindow

router = APIRouter(prefix="/sessions", tags=["Sessions"])


@router.get("")
def list_sessions(
    limit: int = Query(default=30, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    include_active: bool = Query(default=True),
):
    """List sessions with pagination."""
    db = get_db()
    with db.connect() as conn:
        where = "" if include_active else "WHERE is_active = 0"
        rows = conn.execute(
            f"SELECT * FROM sessions {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        total = conn.execute(
            f"SELECT COUNT(*) as cnt FROM sessions {where}"
        ).fetchone()["cnt"]

    sessions = []
    for r in rows:
        d = dict(r)
        # Convert is_active to bool
        d["is_active"] = bool(d["is_active"])
        sessions.append(d)

    return {"data": {"sessions": sessions, "total": total, "limit": limit, "offset": offset}}


@router.get("/current")
def get_current_session():
    """Get the current active session with live elapsed time."""
    db = get_db()
    with db.connect() as conn:
        session = conn.execute(
            "SELECT * FROM sessions WHERE is_active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()

        if not session:
            return {"data": None, "error": "No active session"}

        s = dict(session)

        # Calculate elapsed time
        start_dt = datetime.strptime(s["start_time"], "%Y-%m-%d %H:%M:%S")
        elapsed = int((datetime.now() - start_dt).total_seconds())

        # Get current active window
        active_window = None
        current_activity = conn.execute(
            """SELECT * FROM window_activity
               WHERE session_id = ? AND end_time IS NULL
               ORDER BY id DESC LIMIT 1""",
            (s["id"],),
        ).fetchone()

        if current_activity:
            a = dict(current_activity)
            active_window = {
                "process_name": a["process_name"],
                "window_title": a["window_title"],
                "since": a["start_time"],
            }

        return {
            "data": {
                "id": s["id"],
                "start_time": s["start_time"],
                "elapsed_seconds": elapsed,
                "active_window": active_window,
                "is_active": True,
                "boot_time": s.get("boot_time"),
            }
        }


@router.get("/{session_id}")
def get_session(session_id: int):
    """Get a single session with aggregated activity breakdown."""
    db = get_db()
    with db.connect() as conn:
        session = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()

        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        s = dict(session)
        s["is_active"] = bool(s["is_active"])

        # Get activity summary for this session
        activities = conn.execute(
            """SELECT process_name,
                      COUNT(*) as window_count,
                      COALESCE(SUM(duration_seconds), 0) as total_seconds
               FROM window_activity
               WHERE session_id = ?
               GROUP BY process_name
               ORDER BY total_seconds DESC""",
            (session_id,),
        ).fetchall()

        total_time = sum(a["total_seconds"] for a in activities) if activities else 0

        return {
            "data": {
                "session": s,
                "activity_breakdown": [
                    {
                        "process_name": a["process_name"],
                        "window_count": a["window_count"],
                        "total_seconds": a["total_seconds"],
                        "percentage": round(
                            (a["total_seconds"] / total_time * 100) if total_time > 0 else 0, 1
                        ),
                    }
                    for a in activities
                ],
                "total_active_seconds": total_time,
            }
        }


@router.post("/current/end")
def force_end_current_session():
    """Force-close the current active session."""
    db = get_db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with db.connect() as conn:
        session = conn.execute(
            "SELECT * FROM sessions WHERE is_active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()

        if not session:
            return {"data": None, "error": "No active session to end"}

        s = dict(session)

        # Close activities
        conn.execute(
            """UPDATE window_activity SET end_time = ?,
               duration_seconds = CAST((strftime('%s', ?) - strftime('%s', start_time)) AS INTEGER)
               WHERE session_id = ? AND end_time IS NULL""",
            (now, now, s["id"]),
        )

        # Close session
        conn.execute(
            "UPDATE sessions SET end_time = ?, is_active = 0 WHERE id = ?",
            (now, s["id"]),
        )

    return {"data": {"session_id": s["id"], "end_time": now, "closed": True}}
