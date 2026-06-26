"""System/status endpoints — /api/v1/status and /api/v1/autostart"""

import sys, os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from ..config import load_config
from ..windows_api import get_file_size_bytes
from ..tracker import TrackerState

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PYTHONW = sys.executable.replace("python.exe", "pythonw.exe")

router = APIRouter(prefix="/status", tags=["System"])
autostart_router = APIRouter(prefix="/autostart", tags=["Autostart"])


class AutostartRequest(BaseModel):
    enabled: bool


# This will be set by api.py on startup
_tracker_state: Optional[TrackerState] = None


def set_tracker_state(state: TrackerState):
    global _tracker_state
    _tracker_state = state


@router.get("")
def get_status():
    """Get tracker health status."""
    config = load_config()

    if _tracker_state:
        snapshot = _tracker_state.snapshot()
    else:
        snapshot = {"running": False}

    db_size = get_file_size_bytes(config.database.path)
    log_size = get_file_size_bytes(config.logging.file)

    return {
        "data": {
            "tracker_running": snapshot.get("running", False),
            "current_session_id": snapshot.get("current_session_id"),
            "current_process": snapshot.get("current_process_name"),
            "is_idle": snapshot.get("is_idle", False),
            "last_poll_time": snapshot.get("last_poll_time"),
            "total_polls": snapshot.get("total_polls", 0),
            "start_time": snapshot.get("start_time"),
            "poll_interval": config.tracker.poll_interval_seconds,
            "database_size_bytes": db_size,
            "log_file_size_bytes": log_size,
            "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    }


# --- Autostart (Windows Startup Folder, no admin required) ---

STARTUP_DIR = os.path.join(
    os.environ.get("APPDATA", ""),
    "Microsoft", "Windows", "Start Menu", "Programs", "Startup"
)
STARTUP_VBS = os.path.join(STARTUP_DIR, "PCTimeTracker.vbs")


def _check_autostart_enabled() -> bool:
    """Check if the startup .vbs file exists."""
    return os.path.isfile(STARTUP_VBS)


@autostart_router.get("")
def get_autostart():
    """Check if auto-start is enabled (via Startup folder)."""
    return {"data": {"enabled": _check_autostart_enabled()}}


@autostart_router.post("")
def set_autostart(body: AutostartRequest):
    """Enable or disable auto-start on login via Startup folder. No admin required."""
    if body.enabled:
        try:
            os.makedirs(STARTUP_DIR, exist_ok=True)
            main_script = os.path.join(PROJECT_DIR, "src", "main.py")
            # VBS runs pythonw completely silently — no terminal flash
            vbs_content = (
                f'CreateObject("Wscript.Shell").Run '
                f'"""{PYTHONW}" "{main_script}" run"", 0, False\r\n'
            )
            with open(STARTUP_VBS, "w", encoding="utf-8") as f:
                f.write(vbs_content)
            return {
                "data": {"enabled": True, "message": "已开启开机自启。下次登录时静默启动，不会弹出窗口。"}
            }
        except Exception as e:
            return {
                "data": {"enabled": False, "error": f"写入启动文件失败: {str(e)}"}
            }
    else:
        try:
            if os.path.isfile(STARTUP_VBS):
                os.remove(STARTUP_VBS)
            # Clean up old .bat file if it exists
            old_bat = STARTUP_VBS.replace(".vbs", ".bat")
            if os.path.isfile(old_bat):
                os.remove(old_bat)
            return {
                "data": {"enabled": False, "message": "已关闭开机自启。"}
            }
        except Exception as e:
            return {
                "data": {"enabled": True, "error": f"删除启动文件失败: {str(e)}"}
            }


# --- Backup ---

backup_router = APIRouter(prefix="/backup", tags=["Backup"])


@backup_router.get("")
def list_backups():
    """List all database backups."""
    from ..backup import list_backups as lb
    backups = lb()
    return {"data": {"backups": backups, "count": len(backups)}}


@backup_router.post("")
def create_backup():
    """Manually create a database backup."""
    from ..config import load_config
    from ..backup import backup_database
    config = load_config()
    path = backup_database(config.database.path)
    if path:
        return {"data": {"path": path, "message": "备份完成"}}
    return {"data": {"error": "备份失败——数据库不存在"}}
