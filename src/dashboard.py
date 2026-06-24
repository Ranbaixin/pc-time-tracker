"""Dashboard — mount static HTML dashboard."""

from pathlib import Path

from fastapi.responses import HTMLResponse


_DASHBOARD_PATH = Path(__file__).resolve().parent.parent / "static" / "dashboard.html"


def get_dashboard_html() -> str:
    """Read the dashboard HTML file. Returns the raw content."""
    if _DASHBOARD_PATH.exists():
        return _DASHBOARD_PATH.read_text(encoding="utf-8")
    return "<h1>Dashboard</h1><p>static/dashboard.html not found.</p>"
