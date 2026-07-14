"""FastAPI application — lifespan management, route registration, CORS."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from .config import load_config
from .database import init_db
from .tracker import TrackerEngine
from .dashboard import get_dashboard_html
from .logging_setup import configure_logging
from .runtime import clear_tracker, get_tracker as runtime_get_tracker, set_tracker

from .routes import (
    sessions,
    activity,
    stats,
    agent,
    config_routes,
    system,
    browser_activity,
    classifications,
)

logger = logging.getLogger(__name__)

# Global tracker reference for lifespan management
_tracker: TrackerEngine | None = None


def get_tracker() -> TrackerEngine | None:
    return runtime_get_tracker()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: start tracker on startup, stop on shutdown."""
    global _tracker

    config = load_config()

    # Resolve relative paths against project root (not CWD)
    _base = Path(__file__).resolve().parent.parent

    # Setup logging
    configure_logging(config, base_dir=_base)

    # Initialize database
    init_db(str(_base / config.database.path))
    logger.info(f"Database initialized: {config.database.path}")

    # Start tracker
    _tracker = TrackerEngine(config)
    set_tracker(_tracker)
    _tracker.start()
    logger.info("Tracker engine started")

    # Wire tracker state to system routes
    yield

    # Shutdown
    if _tracker:
        _tracker.stop()
        logger.info("Tracker engine stopped")
    clear_tracker()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    config = load_config()

    app = FastAPI(
        title="PC Time Tracker",
        description="Local Windows PC usage time tracker with REST API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs" if config.server.enable_swagger else None,
        redoc_url=None,
    )

    # CORS — restrict to localhost only (no wildcard origins)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type"],
    )

    # Register routes
    app.include_router(sessions.router, prefix="/api/v1")
    app.include_router(activity.router, prefix="/api/v1")
    app.include_router(stats.router, prefix="/api/v1")
    app.include_router(agent.router, prefix="/api/v1")
    app.include_router(config_routes.router, prefix="/api/v1")
    app.include_router(system.router, prefix="/api/v1")
    app.include_router(system.autostart_router, prefix="/api/v1")
    app.include_router(system.backup_router, prefix="/api/v1")
    app.include_router(browser_activity.router, prefix="/api/v1")
    app.include_router(classifications.router, prefix="/api/v1")
    app.include_router(system.browser_router, prefix="/api/v1")

    # Dashboard route
    if config.server.enable_dashboard:
        @app.get("/", response_class=HTMLResponse)
        async def dashboard():
            return get_dashboard_html()

    logger.info("FastAPI app created with all routes registered")
    return app
