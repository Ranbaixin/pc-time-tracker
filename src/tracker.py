"""Core tracking engine — poll loop, session management, activity recording."""

import atexit
import logging
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .config import AppConfig, TrackerConfig
from .database import get_db
from . import windows_api as win

logger = logging.getLogger(__name__)


class TrackerState:
    """Thread-safe tracker status, readable by the API layer."""

    def __init__(self):
        self._lock = threading.Lock()
        self.running = False
        self.current_session_id: Optional[int] = None
        self.current_window_title: Optional[str] = None
        self.current_process_name: Optional[str] = None
        self.current_activity_since: Optional[str] = None
        self.is_idle = False
        self.last_poll_time: Optional[str] = None
        self.total_polls: int = 0
        self.start_time: Optional[str] = None

    def update(self, **kwargs):
        with self._lock:
            for k, v in kwargs.items():
                if hasattr(self, k):
                    setattr(self, k, v)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "running": self.running,
                "current_session_id": self.current_session_id,
                "current_window_title": self.current_window_title,
                "current_process_name": self.current_process_name,
                "current_activity_since": self.current_activity_since,
                "is_idle": self.is_idle,
                "last_poll_time": self.last_poll_time,
                "total_polls": self.total_polls,
                "start_time": self.start_time,
            }


class SessionManager:
    """Manages session (boot/login) lifecycle in the database."""

    def __init__(self, config: AppConfig):
        self.config = config

    def get_active_session(self) -> Optional[dict]:
        db = get_db()
        with db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE is_active = 1 ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None

    def ensure_session(self) -> dict:
        """Get or create an active session. Handles crash recovery."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        boot_time = win.get_boot_time()

        active = self.get_active_session()

        if active:
            # Check if this is the same boot — if boot_time differs, system rebooted
            if active.get("boot_time") == boot_time:
                logger.info(f"Reusing active session id={active['id']}")
                return active
            else:
                # System was rebooted while tracker wasn't running — close stale session
                logger.info("Boot time mismatch — closing stale session")
                self.close_session(active["id"], reason="system_reboot")

        # Create new session
        db = get_db()
        with db.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO sessions (start_time, is_active, boot_time) VALUES (?, 1, ?)",
                (now, boot_time),
            )
            session_id = cursor.lastrowid
        logger.info(f"Created new session id={session_id}, boot_time={boot_time}")

        return {
            "id": session_id,
            "start_time": now,
            "is_active": True,
            "boot_time": boot_time,
        }

    def close_session(self, session_id: int, reason: str = "shutdown"):
        """Close a session and all its open activities.

        Uses the last activity time as end_time to avoid inflated durations
        when tracker restarts days after the last actual usage."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        db = get_db()
        with db.connect() as conn:
            # Use the last activity time as session end (not "now")
            # to avoid 35-hour sessions when tracker restarts after days
            last_act = conn.execute(
                "SELECT MAX(start_time) as ts FROM window_activity WHERE session_id = ?",
                (session_id,),
            ).fetchone()

            # Compute session end: if last activity was recent (<10 min), use now;
            # otherwise use last activity time to avoid huge session durations
            end_time = now
            if last_act and last_act["ts"]:
                last_ts = datetime.strptime(last_act["ts"], "%Y-%m-%d %H:%M:%S")
                gap = (datetime.now() - last_ts).total_seconds()
                if gap > 600:  # More than 10 minutes since last activity
                    end_time = last_ts.strftime("%Y-%m-%d %H:%M:%S")

            # Close all open window_activity for this session
            conn.execute(
                "UPDATE window_activity SET end_time = ?, duration_seconds = "
                "CAST((strftime('%s', ?) - strftime('%s', start_time)) AS INTEGER) "
                "WHERE session_id = ? AND end_time IS NULL",
                (end_time, end_time, session_id),
            )
            # Close the session
            conn.execute(
                "UPDATE sessions SET end_time = ?, is_active = 0 WHERE id = ?",
                (end_time, session_id),
            )
        logger.info(f"Closed session id={session_id} reason={reason}")


class ActivityRecorder:
    """Records window activity entries."""

    def __init__(self, config: AppConfig):
        self.config = config
        self._current_activity_id: Optional[int] = None

    def start_activity(
        self, session_id: int, process_name: str, window_title: str,
        process_path: Optional[str], tracking_mode: str,
    ) -> int:
        """Insert a new activity row with classification fields. Returns the activity id."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Classify
        from .classifier import get_classifier
        clf = get_classifier()
        info = clf.classify(process_name, window_title, process_path or "", "desktop")

        db = get_db()
        with db.connect() as conn:
            cursor = conn.execute(
                """INSERT INTO window_activity
                   (session_id, window_title, process_name, process_path,
                    start_time, tracking_mode, interaction_count,
                    category, sub_category, site_name, project_name,
                    file_type, content_type, keywords, source,
                    mem_peak_mb, is_fullscreen, battery_pct, power_plugged)
                   VALUES (?, ?, ?, ?, ?, ?, 0,
                           ?, ?, ?, ?, ?, ?, ?, 'desktop', 0, 0, -1, 0)""",
                (session_id, window_title, process_name, process_path, now, tracking_mode,
                 info["category"], info["sub_category"], info["site_name"],
                 info["project_name"], info["file_type"], info["content_type"],
                 info["keywords"]),
            )
            self._current_activity_id = cursor.lastrowid
        return self._current_activity_id

    def close_activity(self) -> Optional[dict]:
        """Close the current open activity. Returns the closed row or None."""
        if self._current_activity_id is None:
            return None

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        db = get_db()
        with db.connect() as conn:
            conn.execute(
                """UPDATE window_activity
                   SET end_time = ?,
                       duration_seconds = CAST(
                           (strftime('%s', ?) - strftime('%s', start_time)) AS INTEGER
                       )
                   WHERE id = ? AND end_time IS NULL""",
                (now, now, self._current_activity_id),
            )
            row = conn.execute(
                "SELECT * FROM window_activity WHERE id = ?", (self._current_activity_id,)
            ).fetchone()

        self._current_activity_id = None
        return dict(row) if row else None

    def update_title(self, window_title: str):
        """Update the window title of the current activity without resetting timer."""
        if self._current_activity_id is None:
            return
        db = get_db()
        with db.connect() as conn:
            conn.execute(
                "UPDATE window_activity SET window_title = ? WHERE id = ? AND end_time IS NULL",
                (window_title, self._current_activity_id),
            )

    def increment_interaction(self):
        """Increment the interaction count for the current activity."""
        if self._current_activity_id is None:
            return
        db = get_db()
        with db.connect() as conn:
            conn.execute(
                "UPDATE window_activity SET interaction_count = interaction_count + 1 "
                "WHERE id = ? AND end_time IS NULL",
                (self._current_activity_id,),
            )

    @property
    def current_activity_id(self) -> Optional[int]:
        return self._current_activity_id


class TrackerEngine:
    """Main tracking engine — runs a polling loop in a background thread."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.state = TrackerState()
        self.session_mgr = SessionManager(config)
        self.activity_recorder = ActivityRecorder(config)
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._reload_event = threading.Event()

        # Tracking state
        self._previous_process: Optional[str] = None
        self._previous_title: Optional[str] = None
        self._previous_idle_ticks: int = 0
        self._idle_since: Optional[str] = None
        self._last_poll_real_time: Optional[float] = None  # for sleep detection

    def start(self):
        """Start the tracker in a background thread."""
        if self._thread and self._thread.is_alive():
            logger.warning("Tracker already running")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="tracker")
        self._thread.start()
        logger.info("Tracker started")

    def stop(self):
        """Signal the tracker to stop and wait for the thread."""
        if not self._thread or not self._thread.is_alive():
            return
        logger.info("Stopping tracker...")
        self._stop_event.set()
        self._thread.join(timeout=10.0)
        self.state.update(running=False)
        logger.info("Tracker stopped")

    def signal_reload(self):
        """Signal the tracker to reload config on the next poll cycle."""
        self._reload_event.set()

    def _run(self):
        """Main polling loop (runs in background thread)."""
        self.state.update(running=True, start_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        # Register cleanup on exit
        atexit.register(self._cleanup)

        # Ensure we have an active session
        session = self.session_mgr.ensure_session()
        self.state.update(current_session_id=session["id"])

        logger.info(f"Tracker loop started. Mode={self.config.tracker.tracking_mode}")

        while not self._stop_event.is_set():
            try:
                t0 = time.time()

                # Handle config reload
                if self._reload_event.is_set():
                    self._reload_event.clear()
                    logger.info("Config reload signaled")

                # 1. Sleep detection
                self._detect_sleep()

                # 2. Check for active session (may have changed after sleep)
                session = self.session_mgr.get_active_session()
                if session is None:
                    session = self.session_mgr.ensure_session()
                self.state.update(current_session_id=session["id"])

                # 3. Idle detection
                idle_ticks = win.get_last_input_ticks()
                tracker_cfg = self.config.tracker
                idle_threshold_ms = tracker_cfg.idle_threshold_seconds * 1000

                is_idle = (
                    tracker_cfg.idle_detection_enabled
                    and idle_ticks > idle_threshold_ms
                )

                self.state.update(is_idle=is_idle)

                if is_idle:
                    # User is idle — don't record activity
                    if not self._previous_idle_ticks or self._previous_idle_ticks <= idle_threshold_ms:
                        # Just became idle — close current activity
                        self.activity_recorder.close_activity()
                        self._previous_process = None
                        self._previous_title = None
                        self._idle_since = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                    self._previous_idle_ticks = idle_ticks
                    self._sleep_until_next_poll(t0, tracker_cfg.poll_interval_seconds)
                    continue

                # Reset idle tracking
                self._previous_idle_ticks = idle_ticks
                self._idle_since = None

                # 4. Get foreground window info
                hwnd = win.get_foreground_window()
                title = win.get_window_text(hwnd) or ""

                if tracker_cfg.ignore_windows_with_empty_title and not title.strip():
                    title = "[No Title]"

                pid = win.get_window_process_id(hwnd)
                process_name = win.get_process_name(pid)

                # 5. Detect window switch (compare by process_name only;
                #    title changes within the same process just update the title)
                if process_name != self._previous_process:
                    # Different process — close previous, start new
                    closed = self.activity_recorder.close_activity()
                    if closed:
                        dur = closed.get("duration_seconds", 0) or 0
                        logger.debug(
                            f"Closed: {closed['process_name']} "
                            f"duration={dur}s interactions={closed.get('interaction_count', 0)}"
                        )

                    process_path = win.get_process_path(pid)
                    self.activity_recorder.start_activity(
                        session_id=session["id"],
                        process_name=process_name,
                        window_title=title,
                        process_path=process_path,
                        tracking_mode=tracker_cfg.tracking_mode,
                    )

                    self._previous_process = process_name
                    self._previous_title = title
                    self.state.update(
                        current_window_title=title,
                        current_process_name=process_name,
                        current_activity_since=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    )
                elif title != self._previous_title:
                    # Same process, title changed — just update the title
                    self.activity_recorder.update_title(title)
                    self._previous_title = title
                    self.state.update(current_window_title=title)

                # 6. In interactive mode, track interaction count
                if tracker_cfg.tracking_mode == "interactive":
                    # If there was input activity in this poll interval, count it
                    if idle_ticks < tracker_cfg.poll_interval_seconds * 1000:
                        self.activity_recorder.increment_interaction()

                # 7. Update state
                self.state.update(
                    last_poll_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    total_polls=self.state.total_polls + 1,
                )

                self._sleep_until_next_poll(t0, tracker_cfg.poll_interval_seconds)

            except Exception:
                logger.exception("Error in tracker loop — continuing")

        # Clean shutdown
        self._shutdown()

    def _detect_sleep(self):
        """Detect if the system went to sleep since the last poll."""
        if self._last_poll_real_time is None:
            self._last_poll_real_time = time.time()
            return

        gap = time.time() - self._last_poll_real_time
        threshold = (
            self.config.tracker.poll_interval_seconds
            * self.config.tracker.sleep_gap_threshold_multiplier
        )

        if gap > threshold:
            logger.info(f"Sleep detected — gap={gap:.0f}s threshold={threshold:.0f}s")

            # Close current activity
            self.activity_recorder.close_activity()
            self._previous_process = None
            self._previous_title = None

            # Record a sleep marker
            session = self.session_mgr.get_active_session()
            if session:
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                sleep_start = datetime.fromtimestamp(
                    self._last_poll_real_time
                ).strftime("%Y-%m-%d %H:%M:%S")

                db = get_db()
                with db.connect() as conn:
                    conn.execute(
                        """INSERT INTO window_activity
                           (session_id, window_title, process_name, start_time, end_time,
                            duration_seconds, tracking_mode)
                           VALUES (?, ?, '[System Sleep]', ?, ?,
                               CAST((strftime('%s', ?) - strftime('%s', ?)) AS INTEGER),
                               ?)""",
                        (session["id"], f"System sleep ({gap:.0f}s)", sleep_start, now,
                         now, sleep_start, self.config.tracker.tracking_mode),
                    )

            # Re-validate session after wake
            self.session_mgr.ensure_session()

        self._last_poll_real_time = time.time()

    def _sleep_until_next_poll(self, t0: float, interval: float):
        """Sleep for the remaining time in this poll cycle."""
        elapsed = time.time() - t0
        sleep_time = max(0.1, interval - elapsed)
        # Use small sleeps so we can respond to stop signals quickly
        while sleep_time > 0 and not self._stop_event.is_set():
            time.sleep(min(0.5, sleep_time))
            sleep_time -= 0.5
            if self._reload_event.is_set():
                break

    def _shutdown(self):
        """Clean shutdown: close activity, session, and backup database."""
        try:
            self.activity_recorder.close_activity()
            session = self.session_mgr.get_active_session()
            if session:
                self.session_mgr.close_session(session["id"], reason="tracker_shutdown")
            # Auto-backup on clean shutdown
            try:
                from .backup import backup_database
                backup_database(self.config.database.path)
            except Exception:
                pass
        except Exception:
            logger.exception("Error during tracker shutdown")

    def _cleanup(self):
        """atexit handler — ensures clean shutdown on unexpected exit."""
        self._shutdown()
        self.state.update(running=False)
