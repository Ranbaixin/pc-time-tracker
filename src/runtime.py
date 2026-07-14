"""Thread-safe process runtime registry."""

from __future__ import annotations

import threading
from typing import Any

_lock = threading.RLock()
_tracker: Any = None


def set_tracker(tracker: Any) -> None:
    global _tracker
    with _lock:
        _tracker = tracker


def get_tracker() -> Any:
    with _lock:
        return _tracker


def clear_tracker() -> None:
    set_tracker(None)


def tracker_snapshot() -> dict:
    tracker = get_tracker()
    return tracker.state.snapshot() if tracker is not None else {"running": False}

