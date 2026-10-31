"""Core tracking engine — poll loop, session management, activity recording."""

import atexit
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

from .config import AppConfig
from .database import get_db
from .collection import CollectionRecorder
from . import windows_api as win

logger = logging.getLogger(__name__)

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


class SystemClock:
    def now(self) -> datetime:
        return datetime.now()

    def monotonic(self) -> float:
        return time.monotonic()


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

    def start_at(
        self, session_id: int, process_name: str, window_title: str,
        process_path: Optional[str], tracking_mode: str, started_at: datetime,
        source: str = "desktop",
    ) -> int:
        """Insert a new activity row. Closes any lingering open activity first."""
        # Safety: close any existing open activity before starting a new one
        if self._current_activity_id is not None:
            self.close_at(started_at)

        now = started_at.strftime(TIME_FORMAT)

        # Classify
        from .classifier import get_classifier
        clf = get_classifier()
        info = clf.classify(process_name, window_title, process_path or "", source)

        db = get_db()
        with db.connect() as conn:
            if source in {"desktop", "system"}:
                conn.execute(
                    """UPDATE window_activity
                       SET end_time = CASE WHEN start_time > ? THEN start_time ELSE ? END,
                           duration_seconds = MAX(0, CAST(
                               strftime('%s', CASE WHEN start_time > ? THEN start_time ELSE ? END)
                               - strftime('%s', start_time) AS INTEGER))
                       WHERE session_id = ? AND end_time IS NULL
                         AND COALESCE(source, 'desktop') IN ('desktop', 'system')""",
                    (now, now, now, now, session_id),
                )
            cursor = conn.execute(
                """INSERT INTO window_activity
                   (session_id, window_title, process_name, process_path,
                    start_time, tracking_mode, interaction_count,
                    category, sub_category, site_name, project_name,
                    file_type, content_type, keywords, source,
                    mem_peak_mb, is_fullscreen, battery_pct, power_plugged)
                   VALUES (?, ?, ?, ?, ?, ?, 0,
                           ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, -1, 0)""",
                (session_id, window_title, process_name, process_path, now, tracking_mode,
                 info["category"], info["sub_category"], info["site_name"],
                 info["project_name"], info["file_type"], info["content_type"],
                 info["keywords"], source),
            )
            self._current_activity_id = cursor.lastrowid
        return self._current_activity_id

    def close_at(self, ended_at: datetime) -> Optional[dict]:
        """Close the current open activity. Returns the closed row or None.
        Also verifies the activity exists and is still open to prevent duplicate closes."""
        if self._current_activity_id is None:
            return None

        now = ended_at.strftime(TIME_FORMAT)
        db = get_db()
        with db.connect() as conn:
            # Verify row still exists and is open
            existing = conn.execute(
                "SELECT id, start_time, end_time FROM window_activity WHERE id = ?",
                (self._current_activity_id,)
            ).fetchone()
            if not existing or existing["end_time"] is not None:
                # Already closed or missing — don't double-close
                self._current_activity_id = None
                return None

            start_dt = datetime.strptime(existing["start_time"], TIME_FORMAT)
            if ended_at < start_dt:
                now = existing["start_time"]

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

    def start_activity(
        self, session_id: int, process_name: str, window_title: str,
        process_path: Optional[str], tracking_mode: str,
    ) -> int:
        return self.start_at(
            session_id, process_name, window_title, process_path, tracking_mode, datetime.now()
        )

    def close_activity(self) -> Optional[dict]:
        return self.close_at(datetime.now())

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

    def __init__(self, config: AppConfig, *, clock=None, windows=None):
        self.config = config
        self._config_lock = threading.RLock()
        self.clock = clock or SystemClock()
        self.collection = CollectionRecorder(self.clock)
        self.windows = windows or win
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
        self._last_poll_monotonic: Optional[float] = None
        self._last_poll_wall_time: Optional[datetime] = None
        self._idle_active = False

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

    def reload_config(self, config: AppConfig):
        with self._config_lock:
            self.config = config
            self.session_mgr.config = config
            self.activity_recorder.config = config
        self.signal_reload()

    def _run(self):
        """Main polling loop (runs in background thread)."""
        self.state.update(running=True, start_time=self.clock.now().strftime(TIME_FORMAT))

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
                # 1. Check for active session
                session = self.session_mgr.get_active_session()
                if session is None:
                    session = self.session_mgr.ensure_session()
                self.state.update(current_session_id=session["id"])
                slept = self._detect_sleep(session["id"])
                if slept:
                    session = self.session_mgr.get_active_session() or self.session_mgr.ensure_session()
                self._poll_once(session["id"])
                tracker_cfg = self.config.tracker
                self._sleep_until_next_poll(t0, tracker_cfg.poll_interval_seconds)

            except Exception:
                logger.exception("Error in tracker loop — continuing")
                self.collection.interrupt()

        # Clean shutdown
        self._shutdown()

    def _poll_once(self, session_id: int):
        now = self.clock.now()
        tracker_cfg = self.config.tracker
        idle_ticks = self.windows.get_last_input_ticks()
        idle_threshold_ms = tracker_cfg.idle_threshold_seconds * 1000
        is_idle = tracker_cfg.idle_detection_enabled and idle_ticks > idle_threshold_ms
        self.state.update(is_idle=is_idle)

        if is_idle:
            if not self._idle_active:
                idle_start = now - timedelta(milliseconds=idle_ticks)
                self.activity_recorder.close_at(idle_start)
                self.activity_recorder.start_at(
                    session_id, "[System Idle]", "User idle", None,
                    tracker_cfg.tracking_mode, idle_start, source="system",
                )
                self._idle_active = True
                self._previous_process = None
                self._previous_title = None
                self._idle_since = idle_start.strftime(TIME_FORMAT)
            self._previous_idle_ticks = idle_ticks
            self._mark_poll(now, session_id, "idle")
            return

        if self._idle_active:
            self.activity_recorder.close_at(now)
            self._idle_active = False
        self._previous_idle_ticks = idle_ticks
        self._idle_since = None

        hwnd = self.windows.get_foreground_window()
        title = self.windows.get_window_text(hwnd) or ""
        if not title.strip():
            if tracker_cfg.ignore_windows_with_empty_title:
                self.activity_recorder.close_at(now)
                self._previous_process = None
                self._previous_title = None
                self._mark_poll(now, session_id, "filtered")
                return
            title = "[No Title]"
        pid = self.windows.get_window_process_id(hwnd)
        process_name = self.windows.get_process_name(pid)

        if process_name != self._previous_process or title != self._previous_title:
            self.activity_recorder.close_at(now)
            self.activity_recorder.start_at(
                session_id, process_name, title, self.windows.get_process_path(pid),
                tracker_cfg.tracking_mode, now,
            )
            self._previous_process = process_name
            self._previous_title = title
            self.state.update(
                current_window_title=title,
                current_process_name=process_name,
                current_activity_since=now.strftime(TIME_FORMAT),
            )
        elif tracker_cfg.tracking_mode == "interactive" and (
            idle_ticks < tracker_cfg.poll_interval_seconds * 1000
        ):
            self.activity_recorder.increment_interaction()
        self._mark_poll(now, session_id, "active")

    def _mark_poll(self, now: datetime, session_id: int, observation: str):
        cfg = self.config.tracker
        self.collection.record(
            now, session_id, observation,
            cfg.poll_interval_seconds * cfg.sleep_gap_threshold_multiplier,
        )
        self._last_poll_wall_time = now
        self._last_poll_monotonic = self.clock.monotonic()
        snapshot = self.state.snapshot()
        self.state.update(
            last_poll_time=now.strftime(TIME_FORMAT),
            total_polls=snapshot["total_polls"] + 1,
        )

    def _detect_sleep(self, session_id: int | None = None) -> bool:
        """Detect if the system went to sleep since the last poll.
        Also detects multi-day gaps (system shutdown/reboot) and cleans up stale activities."""
        current_monotonic = self.clock.monotonic()
        current_wall = self.clock.now()
        if self._last_poll_monotonic is None or self._last_poll_wall_time is None:
            self._last_poll_monotonic = current_monotonic
            self._last_poll_wall_time = current_wall
            return False

        gap = current_monotonic - self._last_poll_monotonic
        threshold = (
            self.config.tracker.poll_interval_seconds
            * self.config.tracker.sleep_gap_threshold_multiplier
        )

        if gap > threshold:
            logger.info(f"Gap detected — gap={gap:.0f}s threshold={threshold:.0f}s")

            # Close any lingering open activity
            sleep_start = self._last_poll_wall_time
            self.activity_recorder.close_at(sleep_start)
            self._previous_process = None
            self._previous_title = None
            self._idle_active = False
            if session_id is None:
                session = self.session_mgr.get_active_session()
                session_id = session["id"] if session else None
            if session_id is not None:
                db = get_db()
                with db.connect() as conn:
                    conn.execute(
                        """INSERT INTO window_activity
                           (session_id, window_title, process_name, start_time, end_time,
                            duration_seconds, tracking_mode, source)
                           VALUES (?, ?, '[System Sleep]', ?, ?,
                               CAST((strftime('%s', ?) - strftime('%s', ?)) AS INTEGER),
                               ?, 'system')""",
                        (session_id, f"System sleep ({gap:.0f}s)",
                         sleep_start.strftime(TIME_FORMAT), current_wall.strftime(TIME_FORMAT),
                         current_wall.strftime(TIME_FORMAT), sleep_start.strftime(TIME_FORMAT),
                         self.config.tracker.tracking_mode),
                    )
            self._last_poll_monotonic = current_monotonic
            self._last_poll_wall_time = current_wall
            return True
        return False

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
            self.collection.interrupt()
            self.activity_recorder.close_activity()
            session = self.session_mgr.get_active_session()
            if session:
                self.session_mgr.close_session(session["id"], reason="tracker_shutdown")
            # Auto-backup on clean shutdown
            try:
                from .backup import backup_database
                backup_database(
                    self.config.database.path, keep=self.config.logging.backup_count
                )
            except Exception:
                pass
        except Exception:
            logger.exception("Error during tracker shutdown")

    def _cleanup(self):
        """atexit handler — ensures clean shutdown on unexpected exit."""
        self._shutdown()
        self.state.update(running=False)
