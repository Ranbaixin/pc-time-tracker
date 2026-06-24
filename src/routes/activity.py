"""Activity endpoints — /api/v1/activity/*"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Query

from ..database import get_db

router = APIRouter(prefix="/activity", tags=["Activity"])


@router.get("")
def list_activity(
    session_id: int = Query(default=None),
    process_name: str = Query(default=None),
    date_from: str = Query(default=None, description="ISO date, e.g. 2026-06-01"),
    date_to: str = Query(default=None, description="ISO date, e.g. 2026-06-22"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    """List activity records with optional filtering."""
    db = get_db()
    conditions = ["1=1"]
    params = []

    if session_id is not None:
        conditions.append("session_id = ?")
        params.append(session_id)
    if process_name:
        conditions.append("process_name = ?")
        params.append(process_name)
    if date_from:
        conditions.append("start_time >= ?")
        params.append(date_from + " 00:00:00")
    if date_to:
        conditions.append("start_time <= ?")
        params.append(date_to + " 23:59:59")

    where = " AND ".join(conditions)

    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM window_activity WHERE {where} "
            f"ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()

        total = conn.execute(
            f"SELECT COUNT(*) as cnt FROM window_activity WHERE {where}", params
        ).fetchone()["cnt"]

    return {
        "data": {
            "activities": [dict(r) for r in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    }


@router.get("/recent")
def get_recent_activity(
    limit: int = Query(default=50, ge=1, le=500),
):
    """Get the most recent activity records."""
    db = get_db()
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM window_activity ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    return {"data": {"activities": [dict(r) for r in rows], "limit": limit}}
