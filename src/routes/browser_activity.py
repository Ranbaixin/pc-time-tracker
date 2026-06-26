"""Browser activity endpoint — receives data from Chrome/Edge extension."""

import logging
from datetime import datetime
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
    for sess in sessions:
        try:
            # Map browser data to window_activity
            duration_sec = max(0, int(sess.duration_ms / 1000))

            # Convert epoch ms to ISO 8601
            if sess.start_time:
                start_dt = datetime.fromtimestamp(sess.start_time / 1000)
                start_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")
            else:
                start_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if sess.end_time and sess.end_time > (sess.start_time or 0):
                end_dt = datetime.fromtimestamp(sess.end_time / 1000)
                end_str = end_dt.strftime("%Y-%m-%d %H:%M:%S")
            else:
                end_str = start_str  # Will be updated later

            process_name = f"[Browser] {sess.domain}"
            window_title = sess.title or sess.url

            # Classify
            info = classifier.classify(
                process_name, window_title, sess.url, source="browser"
            )

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
                        info["site_name"] or sess.domain,
                        info["project_name"], info["file_type"],
                        info["content_type"], info["keywords"],
                    ),
                )
            inserted += 1
        except Exception:
            logger.exception(f"Failed to insert browser activity: {sess.domain}")

    return {"data": {"received": len(sessions), "inserted": inserted}}
