"""Config endpoints — /api/v1/config"""

from fastapi import APIRouter, HTTPException

from ..config import load_config, save_config, AppConfig
from ..runtime import get_tracker

router = APIRouter(prefix="/config", tags=["Config"])


@router.get("")
def get_config():
    """Get current configuration (sensitive values masked)."""
    config = load_config()
    return {"data": config.model_dump()}


@router.put("")
def update_config(updates: dict):
    """Partially update config. Validates and persists to data/config.json."""
    try:
        current = load_config()
        current_dict = current.model_dump()

        # Deep merge the updates
        merged = _deep_merge(current_dict, updates)

        # Validate
        new_config = AppConfig(**merged)

        # Save
        save_config(new_config)

        changed = _changed_paths(current_dict, new_config.model_dump())
        applied = [path for path in changed if path.startswith("tracker.")]
        if "server.browser_integration" in changed:
            applied.append("server.browser_integration")
        restart_required = [path for path in changed if path not in applied]
        tracker = get_tracker()
        if tracker is not None and applied:
            tracker.reload_config(new_config)

        return {
            "data": new_config.model_dump(),
            "message": "Config updated.",
            "applied_immediately": sorted(applied),
            "restart_required": sorted(restart_required),
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid config: {str(e)}") from e


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge override into base."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _changed_paths(before: dict, after: dict, prefix: str = "") -> list[str]:
    changed = []
    for key in sorted(set(before) | set(after)):
        path = f"{prefix}.{key}" if prefix else key
        old = before.get(key)
        new = after.get(key)
        if isinstance(old, dict) and isinstance(new, dict):
            changed.extend(_changed_paths(old, new, path))
        elif old != new:
            changed.append(path)
    return changed
