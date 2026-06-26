"""Database backup — copy tracker.db with timestamps, retain N copies."""

import logging
import os
import shutil
import glob
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def backup_database(db_path: str, backup_dir: Optional[str] = None, keep: int = 30) -> Optional[str]:
    """
    Create a timestamped backup of the database file.

    Args:
        db_path: Path to the tracker.db file
        backup_dir: Directory for backups (default: data/backups/)
        keep: Number of recent backups to retain

    Returns:
        Path to the new backup file, or None if source doesn't exist
    """
    source = Path(db_path)
    if not source.exists():
        logger.warning(f"Database not found: {db_path}")
        return None

    if backup_dir is None:
        backup_dir = source.parent / "backups"

    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    backup_name = f"tracker-{timestamp}.db"
    backup_path = backup_dir / backup_name

    shutil.copy2(source, backup_path)
    logger.info(f"Backup created: {backup_path} ({source.stat().st_size} bytes)")

    # Rotate old backups
    _rotate_backups(backup_dir, keep)

    return str(backup_path)


def _rotate_backups(backup_dir: Path, keep: int):
    """Delete oldest backups beyond the keep limit."""
    backups = sorted(backup_dir.glob("tracker-*.db"), reverse=True)
    for old in backups[keep:]:
        old.unlink()
        logger.debug(f"Removed old backup: {old.name}")


def list_backups(backup_dir: Optional[str] = None) -> list[dict]:
    """List all backups with size and date info."""
    if backup_dir is None:
        backup_dir = "data/backups"

    backup_dir = Path(backup_dir)
    if not backup_dir.exists():
        return []

    backups = sorted(backup_dir.glob("tracker-*.db"), reverse=True)
    return [
        {
            "name": b.name,
            "path": str(b),
            "size_bytes": b.stat().st_size,
            "created": datetime.fromtimestamp(b.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        }
        for b in backups
    ]
