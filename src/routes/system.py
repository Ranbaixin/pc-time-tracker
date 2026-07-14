"""System/status endpoints — /api/v1/status and /api/v1/autostart"""

import os
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import load_config
from ..windows_api import get_file_size_bytes
from ..database import get_db
from ..runtime import tracker_snapshot

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

router = APIRouter(prefix="/status", tags=["System"])
autostart_router = APIRouter(prefix="/autostart", tags=["Autostart"])


class AutostartRequest(BaseModel):
    enabled: bool


# This will be set by api.py on startup
def set_tracker_state(state):
    """Deprecated compatibility shim; runtime registry owns tracker state."""
    return None


@router.get("")
def get_status():
    """Get tracker health status."""
    config = load_config()

    snapshot = tracker_snapshot()

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
            "schema_version": get_db().schema_version(),
        }
    }


# --- Autostart ---


def _check_autostart_enabled() -> bool:
    from ..autostart import get_autostart_service

    return get_autostart_service().is_enabled()


@autostart_router.get("")
def get_autostart():
    """Check if auto-start is enabled."""
    return {"data": {"enabled": _check_autostart_enabled()}}


@autostart_router.post("")
def set_autostart(body: AutostartRequest):
    """Enable or disable the shared generated-launcher autostart service."""
    from ..autostart import get_autostart_service

    service = get_autostart_service()
    try:
        if body.enabled:
            launcher = service.enable()
            return {"data": {"enabled": True, "launcher": str(launcher),
                             "message": "已开启开机自启。"}}
        service.disable()
        return {"data": {"enabled": False, "message": "已关闭开机自启。"}}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"自启动配置失败: {exc}") from exc

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
    path = backup_database(config.database.path, keep=config.logging.backup_count)
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
