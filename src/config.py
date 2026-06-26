"""Configuration system — Pydantic models, JSON load/merge/save."""

import json
import os
import shutil
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


# --- Config models ---

class TrackerConfig(BaseModel):
    tracking_mode: str = Field(default="foreground")
    poll_interval_seconds: float = Field(default=5.0, ge=0.5, le=300.0)
    idle_detection_enabled: bool = True
    idle_threshold_seconds: int = Field(default=600, ge=10, le=3600)
    sleep_gap_threshold_multiplier: float = Field(default=3.0, ge=1.5, le=10.0)
    ignore_windows_with_empty_title: bool = True

    def validate_tracking_mode(self):
        if self.tracking_mode not in ("foreground", "interactive"):
            raise ValueError("tracking_mode must be 'foreground' or 'interactive'")


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=1024, le=65535)
    enable_dashboard: bool = True
    enable_swagger: bool = True
    browser_integration: bool = False


class DatabaseConfig(BaseModel):
    path: str = "data/tracker.db"


class LoggingConfig(BaseModel):
    level: str = "INFO"
    file: str = "logs/tracker.log"
    max_size_mb: int = 10
    backup_count: int = 3


class AppConfig(BaseModel):
    tracker: TrackerConfig = TrackerConfig()
    server: ServerConfig = ServerConfig()
    database: DatabaseConfig = DatabaseConfig()
    logging: LoggingConfig = LoggingConfig()


# --- Config loader ---

def deep_merge(base: dict, override: dict) -> dict:
    """Deep merge override into base. Returns new dict."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def get_config_paths(base_dir: Optional[Path] = None) -> tuple[Path, Path]:
    """Return (default_config_path, user_config_path)."""
    if base_dir is None:
        base_dir = Path(__file__).resolve().parent.parent
    default_config = base_dir / "config" / "default.json"
    user_config = base_dir / "data" / "config.json"
    return default_config, user_config


def load_config(base_dir: Optional[Path] = None) -> AppConfig:
    """
    Load configuration, merging user overrides onto defaults.
    On first run, copies default config to data/config.json.
    """
    if base_dir is None:
        base_dir = Path(__file__).resolve().parent.parent

    default_path, user_path = get_config_paths(base_dir)

    # Load defaults
    with open(default_path, "r", encoding="utf-8") as f:
        defaults = json.load(f)

    # If user config doesn't exist, seed it from defaults
    if not user_path.exists():
        user_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(default_path, user_path)
        return AppConfig(**defaults)

    # Merge user overrides
    with open(user_path, "r", encoding="utf-8") as f:
        user_cfg = json.load(f)

    merged = deep_merge(defaults, user_cfg)

    # Apply environment variable overrides (optional)
    env_overrides = _get_env_overrides()
    if env_overrides:
        merged = deep_merge(merged, env_overrides)

    return AppConfig(**merged)


def save_config(config: AppConfig, base_dir: Optional[Path] = None):
    """Save current config to user config file."""
    if base_dir is None:
        base_dir = Path(__file__).resolve().parent.parent
    _, user_path = get_config_paths(base_dir)
    user_path.parent.mkdir(parents=True, exist_ok=True)
    config_dict = json.loads(config.model_dump_json())
    with open(user_path, "w", encoding="utf-8") as f:
        json.dump(config_dict, f, indent=2, ensure_ascii=False)


def _get_env_overrides() -> dict:
    """Build overrides dict from PCTRACKER_* env vars."""
    overrides: dict = {}
    env_map = {
        "PCTRACKER_SERVER_PORT": ("server", "port", int),
        "PCTRACKER_SERVER_HOST": ("server", "host", str),
        "PCTRACKER_TRACKING_MODE": ("tracker", "tracking_mode", str),
        "PCTRACKER_POLL_INTERVAL": ("tracker", "poll_interval_seconds", float),
        "PCTRACKER_IDLE_THRESHOLD": ("tracker", "idle_threshold_seconds", int),
    }
    for env_var, (section, key, cast) in env_map.items():
        value = os.environ.get(env_var)
        if value is not None:
            overrides.setdefault(section, {})[key] = cast(value)
    return overrides
