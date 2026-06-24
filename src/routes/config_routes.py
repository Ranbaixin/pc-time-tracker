"""Config endpoints — /api/v1/config"""

from fastapi import APIRouter, HTTPException

from ..config import load_config, save_config, AppConfig

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

        return {"data": new_config.model_dump(), "message": "Config updated. Restart or wait for next poll cycle to apply."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid config: {str(e)}")


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge override into base."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
