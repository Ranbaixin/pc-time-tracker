from pathlib import Path

import pytest
from pydantic import ValidationError

from src.config import AppConfig, TrackerConfig, deep_merge, load_config, save_config


def test_tracking_mode_is_validated():
    with pytest.raises(ValidationError):
        TrackerConfig(tracking_mode="invalid")


def test_save_config_is_atomic_and_loadable(tmp_path: Path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "default.json").write_text("{}", encoding="utf-8")

    save_config(AppConfig(), tmp_path)

    saved = tmp_path / "data" / "config.json"
    assert saved.exists()
    assert not saved.with_suffix(".json.tmp").exists()
    assert '"tracker"' in saved.read_text(encoding="utf-8")


def test_load_config_seeds_and_merges_user_and_environment(tmp_path: Path, monkeypatch):
    defaults = tmp_path / "config" / "default.json"
    defaults.parent.mkdir()
    defaults.write_text(
        '{"tracker":{"poll_interval_seconds":5},"server":{"port":8088}}',
        encoding="utf-8",
    )
    first = load_config(tmp_path)
    assert first.server.port == 8088
    user = tmp_path / "data" / "config.json"
    user.write_text('{"tracker":{"poll_interval_seconds":9}}', encoding="utf-8")
    monkeypatch.setenv("PCTRACKER_SERVER_PORT", "9000")

    merged = load_config(tmp_path)

    assert merged.tracker.poll_interval_seconds == 9
    assert merged.server.port == 9000
    assert deep_merge({"a": {"b": 1}}, {"a": {"c": 2}}) == {"a": {"b": 1, "c": 2}}
