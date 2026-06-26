"""Agent-friendly endpoints — /api/v1/agent/*

Designed for AI agents (Claude, GPT, etc.) to consume in a single call.
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Query

from ..database import get_db
from ..config import load_config

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
    today = datetime.now().strftime("%Y-%m-%d")
    config = load_config()
    db = get_db()

    with db.connect() as conn:
        # Current session
        session = conn.execute(
            "SELECT * FROM sessions WHERE is_active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()

        current_session = None
        if session:
            s = dict(session)
            start_dt = datetime.strptime(s["start_time"], "%Y-%m-%d %H:%M:%S")
            elapsed = int((datetime.now() - start_dt).total_seconds())

            current_activity = conn.execute(
                """SELECT * FROM window_activity
                   WHERE session_id = ? AND end_time IS NULL
                   ORDER BY id DESC LIMIT 1""",
                (s["id"],),
            ).fetchone()

            current_session = {
                "id": s["id"],
                "start_time": s["start_time"],
                "elapsed_seconds": elapsed,
                "active_window": {
                    "process_name": current_activity["process_name"],
                    "window_title": current_activity["window_title"],
                    "since": current_activity["start_time"],
                } if current_activity else None,
            }

        # Today summary
        row = conn.execute(
            """SELECT COALESCE(SUM(CASE WHEN process_name != '[System Sleep]'
                                        THEN duration_seconds ELSE 0 END), 0) as total_seconds
               FROM window_activity
               WHERE date(start_time) = ? AND duration_seconds IS NOT NULL""",
            (today,),
        ).fetchone()

        total_seconds = int(row["total_seconds"]) if row else 0

        idle_row = conn.execute(
            """SELECT COALESCE(SUM(duration_seconds), 0) as idle_seconds
               FROM window_activity
               WHERE (process_name = '[System Sleep]' OR window_title LIKE '%sleep%')
                 AND date(start_time) = ? AND duration_seconds IS NOT NULL""",
            (today,),
        ).fetchone()
        idle_seconds = int(idle_row["idle_seconds"]) if idle_row else 0

        top = conn.execute(
            """SELECT process_name, COUNT(*) as window_count,
                      COALESCE(SUM(duration_seconds), 0) as total_seconds
               FROM window_activity
               WHERE process_name NOT IN ('[System Sleep]', '<exited>', '[Protected]')
                 AND date(start_time) = ? AND duration_seconds IS NOT NULL
               GROUP BY process_name ORDER BY total_seconds DESC LIMIT 10""",
            (today,),
        ).fetchall()

        top_processes = []
        for t in top:
            pct = round((t["total_seconds"] / total_seconds * 100) if total_seconds > 0 else 0, 1)
            top_processes.append({
                "process_name": t["process_name"],
                "total_seconds": int(t["total_seconds"]),
                "percentage": pct,
                "window_count": t["window_count"],
            })

        today_summary = {
            "date": today,
            "total_seconds": total_seconds,
            "idle_seconds": idle_seconds,
            "top_processes": top_processes,
        }

        # Last 7 days
        date_from = (datetime.now() - timedelta(days=6)).strftime("%Y-%m-%d")
        daily_rows = conn.execute(
            """SELECT date(start_time) as date,
                      COALESCE(SUM(CASE WHEN process_name != '[System Sleep]'
                                        THEN duration_seconds ELSE 0 END), 0) as total_seconds
               FROM window_activity
               WHERE date(start_time) BETWEEN ? AND ?
                 AND duration_seconds IS NOT NULL
               GROUP BY date(start_time) ORDER BY date ASC""",
            (date_from, today),
        ).fetchall()

        # Fill in missing days
        daily_map = {r["date"]: int(r["total_seconds"]) for r in daily_rows}
        last_7_days = []
        for i in range(7):
            d = (datetime.now() - timedelta(days=6 - i)).strftime("%Y-%m-%d")
            last_7_days.append({
                "date": d,
                "total_seconds": daily_map.get(d, 0),
            })

        # Timeline today
        hour_rows = conn.execute(
            """SELECT CAST(strftime('%H', start_time) AS INTEGER) as hour,
                      COALESCE(SUM(duration_seconds), 0) as active_seconds
               FROM window_activity
               WHERE date(start_time) = ? AND process_name != '[System Sleep]'
                 AND duration_seconds IS NOT NULL
               GROUP BY hour ORDER BY hour""",
            (today,),
        ).fetchall()

        hour_map = {r["hour"]: int(r["active_seconds"]) for r in hour_rows}
        timeline_today = [{"hour": h, "active_seconds": hour_map.get(h, 0)} for h in range(24)]

        # DB metadata
        import os
        db_path = config.database.path
        db_size = os.path.getsize(db_path) if os.path.exists(db_path) else 0

        oldest = conn.execute(
            "SELECT MIN(start_time) as ts FROM window_activity"
        ).fetchone()

        text_summary = _generate_text_summary(today, total_seconds, idle_seconds, top_processes)

        # C7: Classification data
        cat_rows = conn.execute(
            """SELECT category, COALESCE(SUM(duration_seconds), 0) as total
               FROM window_activity WHERE date(start_time) = ? AND category != ''
               GROUP BY category ORDER BY total DESC""",
            (today,),
        ).fetchall()
        categories_today = {r["category"]: int(r["total"]) for r in cat_rows}

        site_rows = conn.execute(
            """SELECT site_name, COALESCE(SUM(duration_seconds), 0) as total, COUNT(*) as visits
               FROM window_activity WHERE date(start_time) = ? AND site_name != ''
               GROUP BY site_name ORDER BY total DESC LIMIT 10""",
            (today,),
        ).fetchall()
        top_sites = [{"name": r["site_name"], "seconds": int(r["total"]), "visits": r["visits"]} for r in site_rows]

        keyword_rows = conn.execute(
            """SELECT keywords FROM window_activity WHERE date(start_time) = ? AND keywords != ''""",
            (today,),
        ).fetchall()
        kw_count: dict[str, int] = {}
        for r in keyword_rows:
            for w in (r["keywords"] or "").split(","):
                w = w.strip()
                if w:
                    kw_count[w] = kw_count.get(w, 0) + 1
        top_keywords = [{"word": w, "count": c} for w, c in sorted(kw_count.items(), key=lambda x: -x[1])[:20]]

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
                "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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
    today = datetime.now().strftime("%Y-%m-%d")
    db = get_db()
    insights = []

    with db.connect() as conn:
        # Workday vs weekend comparison
        weekdays = conn.execute(
            """SELECT COALESCE(AVG(daily_sec), 0) as avg_sec
               FROM (
                   SELECT date(start_time) as d,
                          SUM(duration_seconds) as daily_sec
                   FROM window_activity
                   WHERE process_name != '[System Sleep]'
                     AND duration_seconds IS NOT NULL
                     AND CAST(strftime('%w', start_time) AS INTEGER) BETWEEN 1 AND 5
                   GROUP BY date(start_time)
               )""",
        ).fetchone()

        weekends = conn.execute(
            """SELECT COALESCE(AVG(daily_sec), 0) as avg_sec
               FROM (
                   SELECT date(start_time) as d,
                          SUM(duration_seconds) as daily_sec
                   FROM window_activity
                   WHERE process_name != '[System Sleep]'
                     AND duration_seconds IS NOT NULL
                     AND CAST(strftime('%w', start_time) AS INTEGER) IN (0, 6)
                   GROUP BY date(start_time)
               )""",
        ).fetchone()

        wd_avg = int(weekdays["avg_sec"]) if weekdays else 0
        we_avg = int(weekends["avg_sec"]) if weekends else 0

        wd_h = wd_avg // 3600
        wd_m = (wd_avg % 3600) // 60
        we_h = we_avg // 3600
        we_m = (we_avg % 3600) // 60

        insights.append({
            "key": "workday_vs_weekend",
            "label": "工作日 vs 周末",
            "value": f"工作日平均 {wd_h}h{wd_m}min，周末平均 {we_h}h{we_m}min",
        })

        # Peak hours
        peak = conn.execute(
            """SELECT CAST(strftime('%H', start_time) AS INTEGER) as hour,
                      SUM(duration_seconds) as total_sec
               FROM window_activity
               WHERE process_name != '[System Sleep]'
                 AND duration_seconds IS NOT NULL
               GROUP BY hour
               ORDER BY total_sec DESC
               LIMIT 3""",
        ).fetchall()

        if peak:
            peak_str = "、".join(
                f"{p['hour']}:00-{p['hour']+1}:00" for p in peak
            )
            insights.append({
                "key": "peak_hours",
                "label": "高峰时段",
                "value": f"最活跃时段：{peak_str}",
            })

        # Context switching frequency (window switches per hour)
        avg_switches = conn.execute(
            """SELECT COALESCE(
                       CAST(COUNT(*) AS FLOAT) / NULLIF(COUNT(DISTINCT date(start_time)), 0),
                       0
                   ) as avg_per_day
               FROM window_activity
               WHERE process_name NOT IN ('[System Sleep]', '<exited>')
                 AND duration_seconds IS NOT NULL""",
        ).fetchone()

        if avg_switches and avg_switches["avg_per_day"] > 0:
            switches_per_day = int(avg_switches["avg_per_day"])
            hourly_rate = switches_per_day / (wd_avg / 3600) if wd_avg > 0 else 0
            insights.append({
                "key": "context_switching",
                "label": "窗口切换频率",
                "value": f"日均切换 {switches_per_day} 次（活跃时约 {hourly_rate:.0f} 次/小时）",
            })

        # Today's comparison to average
        today_sec = conn.execute(
            """SELECT COALESCE(SUM(duration_seconds), 0) as s
               FROM window_activity
               WHERE date(start_time) = ? AND process_name != '[System Sleep]'
                 AND duration_seconds IS NOT NULL""",
            (today,),
        ).fetchone()
        today_total = int(today_sec["s"]) if today_sec else 0

        if wd_avg > 0 and today_total > 0:
            diff_pct = round((today_total - wd_avg) / wd_avg * 100)
            direction = "高于" if diff_pct > 0 else "低于"
            insights.append({
                "key": "today_vs_average",
                "label": "今日 vs 平均",
                "value": f"今日使用时长{direction}平均水平 {abs(diff_pct)}%",
            })

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
    db = get_db()

    with db.connect() as conn:
        # Gather data
        today_row = conn.execute(
            """SELECT COALESCE(SUM(CASE WHEN process_name != '[System Sleep]'
                                        THEN duration_seconds ELSE 0 END), 0) as total_seconds
               FROM window_activity
               WHERE date(start_time) = ? AND duration_seconds IS NOT NULL""",
            (today,),
        ).fetchone()
        total_seconds = int(today_row["total_seconds"]) if today_row else 0

        top = conn.execute(
            """SELECT process_name, SUM(duration_seconds) as total_seconds
               FROM window_activity
               WHERE date(start_time) = ?
                 AND process_name NOT IN ('[System Sleep]', '<exited>', '[Protected]')
                 AND duration_seconds IS NOT NULL
               GROUP BY process_name ORDER BY total_seconds DESC LIMIT 10""",
            (today,),
        ).fetchall()

        # 7 day data
        date_from = (datetime.now() - timedelta(days=6)).strftime("%Y-%m-%d")
        week = conn.execute(
            """SELECT date(start_time) as date,
                      COALESCE(SUM(CASE WHEN process_name != '[System Sleep]'
                                        THEN duration_seconds ELSE 0 END), 0) as total_seconds
               FROM window_activity
               WHERE date(start_time) BETWEEN ? AND ?
                 AND duration_seconds IS NOT NULL
               GROUP BY date(start_time) ORDER BY date ASC""",
            (date_from, today),
        ).fetchall()

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
