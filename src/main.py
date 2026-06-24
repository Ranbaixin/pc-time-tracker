"""CLI entry point — command dispatch via argparse."""

import argparse
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).resolve().parent.parent
PYTHONW = sys.executable.replace("python.exe", "pythonw.exe")
TASK_NAME = "PCTimeTracker"


def cmd_run(args):
    """Start both tracker and API server (primary mode)."""
    import uvicorn
    from .config import load_config
    from .api import create_app

    config = load_config()

    # Create the app (this also starts the tracker via lifespan)
    app = create_app()

    print(f"\n  PC Time Tracker starting...")
    print(f"  Dashboard: http://{config.server.host}:{config.server.port}")
    print(f"  API Docs:  http://{config.server.host}:{config.server.port}/docs")
    print(f"  Tracking mode: {config.tracker.tracking_mode}")
    print(f"  Press Ctrl+C to stop\n")

    uvicorn.run(
        app,
        host=config.server.host,
        port=config.server.port,
        log_level=config.logging.level.lower(),
    )


def cmd_track(args):
    """Start tracker only (no API server)."""
    from .config import load_config
    from .database import init_db
    from .tracker import TrackerEngine

    config = load_config()
    init_db(config.database.path)

    tracker = TrackerEngine(config)
    tracker.start()

    print(f"\n  Tracker running (mode: {config.tracker.tracking_mode})")
    print(f"  Poll interval: {config.tracker.poll_interval_seconds}s")
    print(f"  Press Ctrl+C to stop\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n  Stopping tracker...")
        tracker.stop()
        print("  Done.")


def cmd_serve(args):
    """Start API server only (no tracker)."""
    import uvicorn
    from .config import load_config
    from .database import init_db

    config = load_config()
    init_db(config.database.path)

    # Create a minimal app without tracker lifespan
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import HTMLResponse
    from .routes import sessions, activity, stats, agent, config_routes, system
    from .dashboard import get_dashboard_html

    app = FastAPI(
        title="PC Time Tracker (serve-only)",
        version="1.0.0",
        docs_url="/docs" if config.server.enable_swagger else None,
    )
    app.add_middleware(CORSMiddleware, allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?$",
                       allow_credentials=True, allow_methods=["GET", "POST", "PUT"], allow_headers=["Content-Type"])
    app.include_router(sessions.router, prefix="/api/v1")
    app.include_router(activity.router, prefix="/api/v1")
    app.include_router(stats.router, prefix="/api/v1")
    app.include_router(agent.router, prefix="/api/v1")
    app.include_router(config_routes.router, prefix="/api/v1")
    app.include_router(system.router, prefix="/api/v1")
    app.include_router(system.autostart_router, prefix="/api/v1")

    @app.get("/", response_class=HTMLResponse)
    async def dashboard():
        return get_dashboard_html()

    print(f"\n  API server only (no tracking)")
    print(f"  Dashboard: http://{config.server.host}:{config.server.port}")
    print(f"  API Docs:  http://{config.server.host}:{config.server.port}/docs")
    print(f"  Press Ctrl+C to stop\n")

    uvicorn.run(app, host=config.server.host, port=config.server.port,
                log_level=config.logging.level.lower())


def cmd_stats(args):
    """Print quick terminal stats."""
    from .config import load_config
    from .database import init_db, get_db

    config = load_config()
    init_db(config.database.path)
    db = get_db()
    today = datetime.now().strftime("%Y-%m-%d")

    with db.connect() as conn:
        # Today summary
        row = conn.execute(
            """SELECT COALESCE(SUM(CASE WHEN process_name != '[System Sleep]'
                                        THEN duration_seconds ELSE 0 END), 0) as total_seconds,
                      COALESCE(SUM(CASE WHEN process_name = '[System Sleep]'
                                        THEN duration_seconds ELSE 0 END), 0) as idle_seconds
               FROM window_activity
               WHERE date(start_time) = ? AND duration_seconds IS NOT NULL""",
            (today,),
        ).fetchone()

        total = int(row["total_seconds"]) if row else 0
        idle = int(row["idle_seconds"]) if row else 0

        top = conn.execute(
            """SELECT process_name,
                      COALESCE(SUM(duration_seconds), 0) as total_seconds,
                      COUNT(*) as window_count
               FROM window_activity
               WHERE date(start_time) = ?
                 AND process_name NOT IN ('[System Sleep]', '<exited>', '[Protected]')
                 AND duration_seconds IS NOT NULL
               GROUP BY process_name ORDER BY total_seconds DESC LIMIT 10""",
            (today,),
        ).fetchall()

        # Recent sessions
        sessions = conn.execute(
            "SELECT * FROM sessions ORDER BY id DESC LIMIT 5"
        ).fetchall()

    th = total // 3600
    tm = (total % 3600) // 60

    print(f"\n  === PC Time Tracker — {today} ===\n")
    print(f"  活跃时间: {th}h {tm}min")
    if idle > 0:
        ih = idle // 3600
        im = (idle % 3600) // 60
        print(f"  空闲时间: {ih}h {im}min")

    print(f"\n  Top 应用:")
    for i, t in enumerate(top, 1):
        h = t["total_seconds"] // 3600
        m = (t["total_seconds"] % 3600) // 60
        pct = round((t["total_seconds"] / total * 100) if total > 0 else 0, 1)
        print(f"    {i}. {t['process_name']:<20s} {h}h {m}min ({pct}%)  — {t['window_count']} 次切换")

    print(f"\n  最近会话:")
    for s in sessions:
        sd = dict(s)
        print(f"    #{sd['id']}: {sd['start_time']} -> {sd.get('end_time') or '进行中'}")

    print()


def cmd_config(args):
    """Show current configuration."""
    import json
    from .config import load_config

    config = load_config()
    print(json.dumps(config.model_dump(), indent=2, ensure_ascii=False))


def cmd_install(args):
    """Register Windows Task Scheduler for auto-start on login."""
    main_script = str(PROJECT_DIR / "src" / "main.py")

    cmd_parts = [
        "schtasks", "/create",
        "/tn", TASK_NAME,
        "/tr", f'"{PYTHONW}" "{main_script}" run',
        "/sc", "onlogon",
        "/rl", "highest",
        "/f",
    ]

    print(f"  注册开机自启任务...")
    print(f"  任务名: {TASK_NAME}")
    print(f"  命令: {PYTHONW} {main_script} run")

    result = subprocess.run(cmd_parts, capture_output=True, text=True)

    if result.returncode == 0:
        print(f"  ✅ 已成功注册开机自启任务")
        print(f"  提示: 下次登录时自动启动，Python 将以无窗口模式运行")
        print(f"  仪表盘地址: http://127.0.0.1:8080")
    else:
        print(f"  ❌ 注册失败:")
        print(f"  {result.stderr}")

    if result.stdout:
        print(f"  {result.stdout}")


def cmd_uninstall(args):
    """Remove Windows Task Scheduler task."""
    result = subprocess.run(
        ["schtasks", "/delete", "/tn", TASK_NAME, "/f"],
        capture_output=True, text=True,
    )

    if result.returncode == 0:
        print(f"  ✅ 已移除开机自启任务")
    elif "不存在" in result.stderr or "does not exist" in result.stderr.lower():
        print(f"  开机自启任务不存在（可能已移除）")
    else:
        print(f"  ❌ 移除失败: {result.stderr}")


def main():
    parser = argparse.ArgumentParser(
        description="PC Time Tracker — Windows 电脑使用时间追踪",
        prog="python -m src.main",
    )
    sub = parser.add_subparsers(dest="command", help="命令")

    p_run = sub.add_parser("run", help="启动追踪 + API（主模式）")

    p_track = sub.add_parser("track", help="仅追踪（无 API）")

    p_serve = sub.add_parser("serve", help="仅 API 服务（无追踪）")

    p_stats = sub.add_parser("stats", help="终端快速统计")

    p_config = sub.add_parser("config", help="查看当前配置")

    p_install = sub.add_parser("install", help="注册开机自启（Windows Task Scheduler）")

    p_uninstall = sub.add_parser("uninstall", help="移除开机自启任务")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    # Dispatch
    commands = {
        "run": cmd_run,
        "track": cmd_track,
        "serve": cmd_serve,
        "stats": cmd_stats,
        "config": cmd_config,
        "install": cmd_install,
        "uninstall": cmd_uninstall,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
