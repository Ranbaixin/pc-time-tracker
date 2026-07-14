"""Shared rotating logging configuration."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import AppConfig


def configure_logging(config: AppConfig, *, base_dir: Path | None = None) -> None:
    root_dir = base_dir or Path(__file__).resolve().parent.parent
    log_path = Path(config.logging.file)
    if not log_path.is_absolute():
        log_path = root_dir / log_path
    log_path.parent.mkdir(parents=True, exist_ok=True)

    stream = logging.StreamHandler()
    rotating = RotatingFileHandler(
        log_path,
        maxBytes=config.logging.max_size_mb * 1024 * 1024,
        backupCount=config.logging.backup_count,
        encoding="utf-8",
    )
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    stream.setFormatter(formatter)
    rotating.setFormatter(formatter)
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    root.setLevel(getattr(logging, config.logging.level))
    root.addHandler(stream)
    root.addHandler(rotating)

