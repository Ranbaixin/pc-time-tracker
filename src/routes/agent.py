"""Agent-friendly endpoints — /api/v1/agent/*

Designed for AI agents (Claude, GPT, etc.) to consume in a single call.
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Query

from ..database import get_db
from ..collection import get_collection_quality
from ..config import load_config
from .sessions import get_current_session
from .stats import (
    get_categories,
    get_daily_stats,
    get_keywords,
    get_sites,
    get_summary,
    get_timeline,
)

router = APIRouter(prefix="/agent", tags=["Agent"])


def _generate_text_summary(
    date: str, total_seconds: int, idle_seconds: int, top_processes: list
) -> str:
    """Generate a Chinese natural-language summary suitable for AI prompt injection."""
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60

    parts = [f"{date}，电脑活跃使用总计{h}小时{m}分钟。"]

    if idle_seconds > 0:
        ih = idle_seconds // 3600
        im = (idle_seconds % 3600) // 60
        parts.append(f"其中空闲/睡眠时间约{ih}小时{im}分钟。")

    if top_processes:
        parts.append("应用使用排名：")
        for i, p in enumerate(top_processes[:5], 1):
            ph = p["total_seconds"] // 3600
            pm = (p["total_seconds"] % 3600) // 60
            parts.append(
                f"  {i}. {p['process_name']} — {ph}小时{pm}分钟 "
                f"(占{p['percentage']}%)"
            )

    return "\n".join(parts)


@router.get("/context")
def get_agent_context():
    """Comprehensive context snapshot for AI agents. One call, all the data.

    Returns current session, today's summary, 7-day history, timeline,
    and a natural-language text summary ready for prompt injection.
    """
    import os

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    config = load_config()
    db = get_db()
    quality = get_collection_quality(now)
    current_session = get_current_session().get("data")
    current_session_is_live = bool(
        current_session and quality["freshness"] == "fresh"
        and current_session["id"] == quality["last_session_id"]
    )
    if current_session and not current_session_is_live:
        current_session["active_window"] = None
    today_summary = get_summary(today)["data"]
    last_7_days = get_daily_stats(days=7, date_from=None, date_to=None)["data"]["daily"]
    timeline_today = get_timeline(today)["data"]["timeline"]
    categories_today = get_categories(today)["data"]["categories"]
    site_data = get_sites(today, 10)["data"]["sites"]
    top_sites = [{"name": x["name"], "seconds": x["total_seconds"], "visits": x["visits"]}
                 for x in site_data]
    top_keywords = get_keywords(1, 20)["data"]["keywords"]
    total_seconds = today_summary["total_seconds"]
    idle_seconds = today_summary["idle_seconds"]
    top_processes = today_summary["top_processes"]
    text_summary = _generate_text_summary(today, total_seconds, idle_seconds, top_processes)
    if any(window["status"] != "complete" for window in quality["coverage"].values()) or (
        not current_session_is_live
    ):
        text_summary += (
            "\n数据可信度提示：采集状态=" + quality["collection_status"]
            + "；今日覆盖=" + quality["coverage"]["today"]["status"]
            + "；七日覆盖=" + quality["coverage"]["last_7_days"]["status"]
            + "。统计仅代表已记录数据；未覆盖区间不能解释为休息，开放历史记录可能延长统计。"
        )
    db_path = config.database.path
    db_size = os.path.getsize(db_path) if os.path.exists(db_path) else 0
    with db.connect() as conn:
        oldest = conn.execute("SELECT MIN(start_time) as ts FROM window_activity").fetchone()

    return {
        "data": {
            "current_session": current_session,
            "today_summary": today_summary,
            "last_7_days": last_7_days,
            "timeline_today": timeline_today,
            "text_summary": text_summary,
            "tracking_mode": config.tracker.tracking_mode,
            "categories_today": categories_today,
            "top_sites": top_sites,
            "top_keywords": top_keywords,
            "_metadata": {
                "db_size_bytes": db_size,
                "oldest_record": oldest["ts"] if oldest else None,
                "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                "collection_quality": quality,
                "current_session_is_live": current_session_is_live,
            },
        }
    }


@router.get("/raw")
def get_raw_data(
    days: int = Query(default=7, ge=1, le=90),
    table: str = Query(default="all"),
):
    """Export raw data for AI processing.

    Args:
        days: Number of days of data to return
        table: 'sessions', 'activity', or 'all'
    """
    date_from = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    db = get_db()

    result: dict = {"date_from": date_from, "generated_at": datetime.now().isoformat()}

    with db.connect() as conn:
        if table in ("all", "sessions"):
            sessions = conn.execute(
                """SELECT * FROM sessions
                   WHERE date(start_time) >= ? OR date(end_time) >= ?
                   ORDER BY id DESC""",
                (date_from, date_from),
            ).fetchall()
            result["sessions"] = [dict(r) for r in sessions]

        if table in ("all", "activity"):
            activities = conn.execute(
                """SELECT id, session_id, window_title, process_name, start_time,
                          end_time, duration_seconds, tracking_mode, interaction_count
                   FROM window_activity
                   WHERE date(start_time) >= ?
                   ORDER BY id""",
                (date_from,),
            ).fetchall()
            result["activities"] = [dict(r) for r in activities]

    return {"data": result}


@router.get("/insights")
def get_insights():
    """Pre-computed insights for AI analysis.

    Returns patterns, anomalies, and trends.
    """
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    date_from = (now - timedelta(days=29)).strftime("%Y-%m-%d")
    daily = get_daily_stats(days=30, date_from=date_from, date_to=today)["data"]["daily"]
    weekdays = [x["total_seconds"] for x in daily
                if datetime.strptime(x["date"], "%Y-%m-%d").weekday() < 5]
    weekends = [x["total_seconds"] for x in daily
                if datetime.strptime(x["date"], "%Y-%m-%d").weekday() >= 5]
    wd_avg = int(sum(weekdays) / len(weekdays)) if weekdays else 0
    we_avg = int(sum(weekends) / len(weekends)) if weekends else 0
    insights = [{
        "key": "workday_vs_weekend", "label": "工作日 vs 周末",
        "value": (f"工作日平均 {wd_avg // 3600}h{(wd_avg % 3600) // 60}min，"
                  f"周末平均 {we_avg // 3600}h{(we_avg % 3600) // 60}min"),
    }]

    hour_totals = [0] * 24
    for item in daily:
        timeline = get_timeline(item["date"])["data"]["timeline"]
        for hour in timeline:
            hour_totals[hour["hour"]] += hour["active_seconds"]
    peak_hours = sorted(range(24), key=lambda h: hour_totals[h], reverse=True)[:3]
    if any(hour_totals):
        insights.append({"key": "peak_hours", "label": "高峰时段",
                         "value": "最活跃时段：" + "、".join(
                             f"{hour}:00-{hour + 1}:00" for hour in peak_hours)})

    today_total = get_summary(today)["data"]["total_seconds"]
    if wd_avg and today_total:
        diff_pct = round((today_total - wd_avg) / wd_avg * 100)
        insights.append({"key": "today_vs_average", "label": "今日 vs 平均",
                         "value": f"今日使用时长{'高于' if diff_pct > 0 else '低于'}平均水平 {abs(diff_pct)}%"})

    return {
        "data": {
            "insights": insights,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    }


@router.get("/prompt")
def get_prompt(
    scenario: str = Query(
        default="daily_review",
        description="Scenario: daily_review, weekly_report, productivity_analysis, habit_tracking",
    ),
):
    """Return a pre-filled AI prompt with data injected.

    Use this endpoint to get a ready-to-use prompt you can paste into any AI chat.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    summary = get_summary(today)["data"]
    total_seconds = summary["total_seconds"]
    top = summary["top_processes"]
    week = get_daily_stats(days=7, date_from=None, date_to=None)["data"]["daily"]

    # Build data string
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    data_str = f"日期：{today}\n总使用时间：{h}小时{m}分钟\n\n应用分布：\n"
    for i, p in enumerate(top, 1):
        ph = p["total_seconds"] // 3600
        pm = (p["total_seconds"] % 3600) // 60
        data_str += f"  {i}. {p['process_name']}: {ph}h{pm}min\n"

    data_str += "\n本周趋势：\n"
    for w in week:
        wh = w["total_seconds"] // 3600
        wm = (w["total_seconds"] % 3600) // 60
        data_str += f"  {w['date']}: {wh}h{wm}min\n"

    prompts = {
        "daily_review": f"""你是一位个人生产力教练。请根据以下我今天的电脑使用数据，给我一个简洁的每日回顾：

{data_str}

请分析：
1. 今天的总体使用情况如何？
2. 时间分配是否合理？
3. 有什么值得注意的模式或问题？
4. 明天可以改进的一两个建议。

用中文回答，语气友好但有洞察力。""",

        "weekly_report": f"""你是一位数据分析师。请根据以下我本周的电脑使用数据，生成一份周报：

{data_str}

请分析：
1. 本周使用趋势（哪天最活跃/最不活跃）
2. 主要应用的使用模式
3. 工作日模式 vs 休息日模式
4. 下周建议

用中文回答，提供具体的数字和可操作的建议。""",

        "productivity_analysis": f"""你是一位生产力专家。请分析以下电脑使用数据，评估我的工作效率：

{data_str}

请分析：
1. 深度工作的时段（长时间专注单一应用）
2. 分心/多任务切换的模式
3. 高峰生产力时段
4. 改进深度工作能力的建议

用中文回答，引用数据中的具体数字。""",

        "habit_tracking": f"""你是一位习惯养成教练。请分析以下电脑使用数据，帮我追踪和改善数字习惯：

{data_str}

请分析：
1. 是否有不健康的长时间使用模式？
2. 哪些应用可能占用了过多时间？
3. 生活-工作边界的清晰度（晚间使用等）
4. 养成更好数字习惯的具体步骤

用中文回答，给出温暖但诚实的反馈。""",
    }

    prompt = prompts.get(scenario, prompts["daily_review"])

    return {
        "data": {
            "scenario": scenario,
            "prompt": prompt,
            "usage_note": "将此 prompt 复制粘贴到任何 AI 对话中即可。",
        }
    }
