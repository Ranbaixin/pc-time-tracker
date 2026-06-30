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


# --- Autostart (Windows Registry Run key — most reliable) ---

import winreg

STARTUP_DIR = os.path.join(
    os.environ.get("APPDATA", ""),
    "Microsoft", "Windows", "Start Menu", "Programs", "Startup"
)
STARTUP_VBS = os.path.join(STARTUP_DIR, "PCTimeTracker.vbs")
REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
REG_VALUE = "PCTimeTracker"


def _check_autostart_enabled() -> bool:
    """Check if autostart is registered in Windows Registry."""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_READ)
        try:
            value, _ = winreg.QueryValueEx(key, REG_VALUE)
            return bool(value)
        except FileNotFoundError:
            return False
        finally:
            winreg.CloseKey(key)
    except Exception:
        return os.path.isfile(STARTUP_VBS)


@autostart_router.get("")
def get_autostart():
    """Check if auto-start is enabled."""
    return {"data": {"enabled": _check_autostart_enabled()}}


@autostart_router.post("")
def set_autostart(body: AutostartRequest):
    """Enable/disable auto-start via Windows Registry Run key (most reliable)."""
    if body.enabled:
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_SET_VALUE)
            cmd = f'cmd /c "cd /d {PROJECT_DIR} && {PYTHONW} -m src.main run"'
            winreg.SetValueEx(key, REG_VALUE, 0, winreg.REG_SZ, cmd)
            winreg.CloseKey(key)
            return {"data": {"enabled": True, "message": "已开启开机自启（注册表）。"}}
        except Exception as e:
            return {"data": {"enabled": False, "error": str(e)}}
    else:
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_SET_VALUE)
            try:
                winreg.DeleteValue(key, REG_VALUE)
            except FileNotFoundError:
                pass
            winreg.CloseKey(key)
        except Exception:
            pass
        for f in [STARTUP_VBS, STARTUP_VBS.replace(".vbs", ".bat")]:
            if os.path.isfile(f):
                os.remove(f)
        return {"data": {"enabled": False, "message": "已关闭开机自启。"}}

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


# --- Browser Integration Toggle ---

browser_router = APIRouter(prefix="/browser-integration", tags=["Browser Integration"])


@browser_router.get("")
def get_browser_integration():
    config = load_config()
    return {"data": {"enabled": config.server.browser_integration}}


@browser_router.post("")
def set_browser_integration(body: AutostartRequest):
    """Enable/disable browser plugin data sync."""
    from ..config import load_config, save_config
    config = load_config()
    config.server.browser_integration = body.enabled
    save_config(config)
    msg = "浏览器插件数据同步已开启" if body.enabled else "已关闭——使用桌面窗口检测"
    return {"data": {"enabled": body.enabled, "message": msg}}
