import logging
from pathlib import Path

from src.config import AppConfig
from src.logging_setup import configure_logging
from src.routes import config_routes
from src.runtime import clear_tracker, set_tracker


class FakeTracker:
    def __init__(self):
        self.reloaded = None

    def reload_config(self, config):
        self.reloaded = config


def test_config_update_applies_tracker_fields_and_marks_restart(monkeypatch):
    current = AppConfig()
    saved = []
    tracker = FakeTracker()
    set_tracker(tracker)
    monkeypatch.setattr(config_routes, "load_config", lambda: current)
    monkeypatch.setattr(config_routes, "save_config", saved.append)

    result = config_routes.update_config({"tracker": {"poll_interval_seconds": 2},
                                          "server": {"port": 9000}})

    assert tracker.reloaded.tracker.poll_interval_seconds == 2
    assert result["applied_immediately"] == ["tracker.poll_interval_seconds"]
    assert result["restart_required"] == ["server.port"]
    assert saved[0].server.port == 9000
    clear_tracker()


def test_rotating_logging_creates_directory_and_rotates(tmp_path: Path):
    config = AppConfig()
    config.logging.file = str(tmp_path / "nested" / "tracker.log")
    config.logging.max_size_mb = 1
    config.logging.backup_count = 2

    configure_logging(config, base_dir=tmp_path)
    logger = logging.getLogger("rotation-test")
    logger.info("started")

    assert (tmp_path / "nested" / "tracker.log").exists()
    handlers = logging.getLogger().handlers
    assert any(getattr(handler, "backupCount", None) == 2 for handler in handlers)
