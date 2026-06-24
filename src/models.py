"""Shared data models — Pydantic schemas for API requests and responses."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# --- DB row models (as returned from SQLite) ---

class SessionRow(BaseModel):
    id: int
    start_time: str
    end_time: Optional[str] = None
    is_active: bool = True
    boot_time: Optional[str] = None
    created_at: Optional[str] = None


class WindowActivityRow(BaseModel):
    id: int
    session_id: int
    window_title: str = ""
    process_name: str
    process_path: Optional[str] = None
    start_time: str
    end_time: Optional[str] = None
    duration_seconds: Optional[int] = None
    tracking_mode: str = "foreground"
    interaction_count: int = 0


# --- API response models ---

class ActiveWindow(BaseModel):
    process_name: str
    window_title: str
    since: str  # ISO 8601


class CurrentSession(BaseModel):
    id: int
    start_time: str
    elapsed_seconds: int
    active_window: Optional[ActiveWindow] = None
    is_idle: bool = False


class ProcessStat(BaseModel):
    process_name: str
    total_seconds: int
    percentage: float = 0.0
    window_count: int = 0


class DaySummary(BaseModel):
    date: str
    total_seconds: int
    idle_seconds: int = 0
    top_processes: list[ProcessStat] = []


class TimelineEntry(BaseModel):
    hour: int
    active_seconds: int
    top_process: Optional[str] = None


class DailyStat(BaseModel):
    date: str
    total_seconds: int
    idle_seconds: int = 0
    top_process: Optional[str] = None


class StatusInfo(BaseModel):
    tracker_running: bool
    current_session_id: Optional[int] = None
    uptime_seconds: int
    poll_interval: float
    database_size_bytes: int
    log_file_size_bytes: int = 0


# --- Agent API models ---

class AgentContext(BaseModel):
    current_session: Optional[CurrentSession] = None
    today_summary: Optional[DaySummary] = None
    last_7_days: list[DailyStat] = []
    timeline_today: list[TimelineEntry] = []
    text_summary: str = ""
    tracking_mode: str = "foreground"
    metadata: dict = {}


class AgentInsight(BaseModel):
    key: str
    label: str
    value: str
    detail: Optional[str] = None


class AgentInsights(BaseModel):
    insights: list[AgentInsight] = []
    generated_at: str = ""


# --- API response envelope ---

class APIResponse(BaseModel):
    data: Optional[dict | list] = None
    error: Optional[str] = None


# --- Helpers ---

def row_to_dict(row) -> dict:
    """Convert sqlite3.Row to dict."""
    if row is None:
        return None
    return dict(row)
