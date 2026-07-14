from argparse import Namespace
from types import SimpleNamespace

import pytest

from src import main
from src.config import AppConfig


def patch_runtime(monkeypatch):
    config = AppConfig()
    monkeypatch.setattr("src.config.load_config", lambda: config)
    monkeypatch.setattr("src.database.init_db", lambda path: None)
    monkeypatch.setattr("src.logging_setup.configure_logging", lambda *args, **kwargs: None)
    return config


def test_run_and_serve_commands_build_apps_without_starting_server(monkeypatch):
    config = patch_runtime(monkeypatch)
    calls = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: calls.append((app, kwargs)))

    main.cmd_run(Namespace())
    main.cmd_serve(Namespace())

    assert len(calls) == 2
    assert all(call[1]["port"] == config.server.port for call in calls)


def test_track_command_starts_and_stops_engine(monkeypatch):
    patch_runtime(monkeypatch)
    events = []

    class FakeEngine:
        def __init__(self, config):
            events.append("init")

        def start(self):
            events.append("start")

        def stop(self):
            events.append("stop")

    monkeypatch.setattr("src.tracker.TrackerEngine", FakeEngine)
    monkeypatch.setattr(main.time, "sleep", lambda seconds: (_ for _ in ()).throw(KeyboardInterrupt))

    main.cmd_track(Namespace())

    assert events == ["init", "start", "stop"]


def test_config_backup_and_autostart_commands(monkeypatch, capsys):
    patch_runtime(monkeypatch)
    monkeypatch.setattr("src.backup.backup_database", lambda *args, **kwargs: "backup.db")
    monkeypatch.setattr("src.backup.list_backups", lambda: [
        {"name": "backup.db", "size_bytes": 1024, "created": "now"}
    ])
    service = SimpleNamespace(
        enable=lambda: "launcher.vbs",
        disable=lambda: None,
    )
    monkeypatch.setattr("src.autostart.get_autostart_service", lambda: service)

    main.cmd_config(Namespace())
    main.cmd_backup(Namespace())
    main.cmd_install(Namespace())
    main.cmd_uninstall(Namespace())

    output = capsys.readouterr().out
    assert '"tracker"' in output
    assert "backup.db" in output
    assert "8088" in output


def test_autostart_command_failure_returns_nonzero(monkeypatch):
    def fail():
        raise OSError("denied")

    monkeypatch.setattr(
        "src.autostart.get_autostart_service",
        lambda: SimpleNamespace(enable=fail, disable=fail),
    )
    with pytest.raises(SystemExit) as install_error:
        main.cmd_install(Namespace())
    with pytest.raises(SystemExit) as uninstall_error:
        main.cmd_uninstall(Namespace())
    assert install_error.value.code == 1
    assert uninstall_error.value.code == 1
