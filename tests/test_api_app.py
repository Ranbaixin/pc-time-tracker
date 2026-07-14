from src import api
from src.config import AppConfig


def test_create_app_respects_dashboard_flag_and_registers_api(monkeypatch):
    disabled = AppConfig()
    disabled.server.enable_dashboard = False
    monkeypatch.setattr(api, "load_config", lambda: disabled)
    app = api.create_app()
    paths = {route.path for route in app.routes}
    assert "/" not in paths
    assert "/api/v1/stats/summary" in paths

    enabled = AppConfig()
    enabled.server.enable_dashboard = True
    monkeypatch.setattr(api, "load_config", lambda: enabled)
    app = api.create_app()
    assert "/" in {route.path for route in app.routes}
