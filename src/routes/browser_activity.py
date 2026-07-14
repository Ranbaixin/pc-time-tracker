"""Browser activity endpoint — receives data from Chrome/Edge extension."""

import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..database import get_db
from ..classifier import get_classifier
from ..config import load_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/activity/browser", tags=["Browser Activity"])


class BrowserSession(BaseModel):
    """Single browser session sent by the extension."""
    url: str
    domain: str
    title: str = ""
    duration_ms: int
    start_time: Optional[float] = None  # epoch ms
    end_time: Optional[float] = None    # epoch ms


class BrowserBatch(BaseModel):
    """Batch of browser sessions (sent every ~30s or on tab switch)."""
    sessions: list[BrowserSession] = Field(default_factory=list)


@router.post("")
def receive_browser_activity(payload: BrowserBatch | BrowserSession):
    """Receive browser activity data from the Chrome/Edge extension.

    Accepts both a single session or a batch of sessions.
    Each session is mapped to a window_activity row with source='browser'.
    Returns 204 if browser integration is disabled (plugin should fallback gracefully).
    """
    config = load_config()
    if not config.server.browser_integration:
        return {"data": {"received": 0, "message": "Browser integration disabled", "enabled": False}}

    sessions = payload.sessions if isinstance(payload, BrowserBatch) else [payload]
    if not sessions:
        return {"data": {"received": 0, "message": "Empty batch"}}

    db = get_db()
    classifier = get_classifier()

    # Get current active session
    with db.connect() as conn:
        active = conn.execute(
            "SELECT id FROM sessions WHERE is_active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
        session_id = active["id"] if active else None

    if session_id is None:
        logger.warning("No active session for browser activity — creating ad-hoc session")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with db.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO sessions (start_time, is_active) VALUES (?, 1)", (now,)
            )
            session_id = cursor.lastrowid

    inserted = 0
    errors = []
    single = isinstance(payload, BrowserSession)
    for index, sess in enumerate(sessions):
        try:
            # Map browser data to window_activity
            if not 0 <= sess.duration_ms <= 86_400_000:
                raise ValueError("duration_ms must be between 0 and 86400000")
            duration_sec = int(sess.duration_ms / 1000)
            now = datetime.now()

            # Convert epoch ms to ISO 8601
            if sess.start_time:
                start_dt = datetime.fromtimestamp(sess.start_time / 1000)
                if start_dt > now + timedelta(minutes=5):
                    raise ValueError("start_time cannot be in the future")
            else:
                start_dt = now - timedelta(milliseconds=sess.duration_ms)

            if sess.end_time:
                end_dt = datetime.fromtimestamp(sess.end_time / 1000)
                if end_dt <= start_dt:
                    raise ValueError("end_time must be after start_time")
                if end_dt > now + timedelta(minutes=5):
                    raise ValueError("end_time cannot be in the future")
            else:
                end_dt = start_dt + timedelta(milliseconds=sess.duration_ms)
            start_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")
            end_str = end_dt.strftime("%Y-%m-%d %H:%M:%S")

            process_name = f"[Browser] {sess.domain}"
            window_title = sess.title or sess.url

            # Classify — use exact domain as site_name; classifier may refine it
            info = classifier.classify(
                process_name, window_title, sess.url, source="browser"
            )
            # Always prefer the exact domain from the browser extension.
            # This keeps site_name consistent (e.g. "github.com" not "GitHub").
            if not info["site_name"]:
                info["site_name"] = sess.domain

            with db.connect() as conn:
                conn.execute(
                    """INSERT INTO window_activity
                       (session_id, window_title, process_name, process_path,
                        start_time, end_time, duration_seconds, tracking_mode,
                        source, category, sub_category, site_name, project_name,
                        file_type, content_type, keywords)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'browser',
                               'browser', ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        session_id, window_title, process_name, sess.url,
                        start_str, end_str, duration_sec,
                        info["category"], info["sub_category"],
                        sess.domain,   # exact domain, not classifier's fuzzy match
                        info["project_name"], info["file_type"],
                        info["content_type"], info["keywords"],
                    ),
                )
            inserted += 1
        except ValueError as exc:
            if single:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            errors.append({"index": index, "reason": str(exc)})
        except Exception as exc:
            logger.exception(f"Failed to insert browser activity: {sess.domain}")
            if single:
                raise HTTPException(
                    status_code=500, detail="Failed to store browser activity"
                ) from exc
            errors.append({"index": index, "reason": "storage failure"})

    return {"data": {"received": len(sessions), "inserted": inserted, "errors": errors}}
