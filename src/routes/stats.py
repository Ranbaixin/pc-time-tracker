"""Statistics endpoints backed by the normalized interval query layer."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, Query

from ..statistics import activity_timeline, browser_timeline, day_bounds

router = APIRouter(prefix="/stats", tags=["Statistics"])


def _resolve_date(date_str: str) -> str:
    if date_str and date_str.lower() == "today":
        return datetime.now().strftime("%Y-%m-%d")
    return date_str or datetime.now().strftime("%Y-%m-%d")


def _active(items):
    return [item for item in items if not item.is_idle]


def _group_seconds(items, attribute: str):
    totals: dict[str, int] = defaultdict(int)
    ids: dict[str, set[int]] = defaultdict(set)
    for item in items:
        key = getattr(item, attribute) or ""
        if key:
            totals[key] += item.duration_seconds
            ids[key].add(item.activity_id)
    return totals, ids


@router.get("/summary")
def get_summary(date: str = Query(default="today", description="Date in YYYY-MM-DD or 'today'")):
    date = _resolve_date(date)
    start, end = day_bounds(date)
    timeline = activity_timeline(start, end)
    active = _active(timeline)
    total_seconds = sum(x.duration_seconds for x in active)
    idle_seconds = sum(x.duration_seconds for x in timeline if x.is_idle)
    totals, ids = _group_seconds(active, "process_name")
    top = sorted(totals.items(), key=lambda x: (-x[1], x[0]))[:10]
    return {"data": {
        "date": date,
        "total_seconds": total_seconds,
        "idle_seconds": idle_seconds,
        "window_count": len({x.activity_id for x in active}),
        "top_processes": [
            {"process_name": name, "total_seconds": seconds,
             "percentage": round(seconds / total_seconds * 100, 1) if total_seconds else 0,
             "window_count": len(ids[name])}
            for name, seconds in top
        ],
    }}


@router.get("/daily")
def get_daily_stats(
    days: int = Query(default=7, ge=1, le=90),
    date_from: str = Query(default=None),
    date_to: str = Query(default=None),
):
    if not (date_from and date_to):
        date_to = datetime.now().strftime("%Y-%m-%d")
        date_from = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    current = datetime.strptime(date_from, "%Y-%m-%d")
    last = datetime.strptime(date_to, "%Y-%m-%d")
    result = []
    while current <= last:
        start, end = current, current + timedelta(days=1)
        timeline = activity_timeline(start, end)
        active = _active(timeline)
        totals, _ = _group_seconds(active, "process_name")
        top = max(totals, key=totals.get) if totals else None
        result.append({
            "date": current.strftime("%Y-%m-%d"),
            "total_seconds": sum(x.duration_seconds for x in active),
            "idle_seconds": sum(x.duration_seconds for x in timeline if x.is_idle),
            "top_process": top,
        })
        current += timedelta(days=1)
    return {"data": {"daily": result, "date_from": date_from, "date_to": date_to}}


@router.get("/processes")
def get_top_processes(
    date: str = Query(default="today"),
    date_from: str = Query(default=None),
    date_to: str = Query(default=None),
    limit: int = Query(default=10, ge=1, le=50),
):
    if not (date_from and date_to):
        date_from = date_to = _resolve_date(date)
    start = datetime.strptime(date_from, "%Y-%m-%d")
    end = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
    active = _active(activity_timeline(start, end))
    total = sum(x.duration_seconds for x in active)
    totals, ids = _group_seconds(active, "process_name")
    ordered = sorted(totals.items(), key=lambda x: (-x[1], x[0]))[:limit]
    return {"data": {
        "processes": [
            {"process_name": name, "total_seconds": seconds,
             "percentage": round(seconds / total * 100, 1) if total else 0,
             "window_count": len(ids[name])}
            for name, seconds in ordered
        ],
        "date_from": date_from, "date_to": date_to,
    }}


@router.get("/timeline")
def get_timeline(date: str = Query(default="today")):
    date = _resolve_date(date)
    start, end = day_bounds(date)
    active = _active(activity_timeline(start, end))
    hours = []
    for hour in range(24):
        hs = start + timedelta(hours=hour)
        he = hs + timedelta(hours=1)
        proc: dict[str, int] = defaultdict(int)
        for item in active:
            seconds = max(0, int((min(item.end, he) - max(item.start, hs)).total_seconds()))
            if seconds:
                proc[item.process_name] += seconds
        total = sum(proc.values())
        hours.append({"hour": hour, "active_seconds": total,
                      "top_process": max(proc, key=proc.get) if proc else None})
    return {"data": {"date": date, "timeline": hours}}


@router.get("/categories")
def get_categories(date: str = Query(default="today")):
    date = _resolve_date(date)
    start, end = day_bounds(date)
    active = _active(activity_timeline(start, end))
    categories, category_ids = _group_seconds(active, "category")
    sub_categories, _ = _group_seconds(active, "sub_category")
    ordered = sorted(categories.items(), key=lambda x: (-x[1], x[0]))
    return {"data": {"date": date, "categories": dict(ordered),
                     "sub_categories": dict(sorted(sub_categories.items(), key=lambda x: -x[1])),
                     "details": [{"category": key, "total_seconds": value,
                                  "count": len(category_ids[key])} for key, value in ordered]}}


@router.get("/sites")
def get_sites(date: str = Query(default="today"), limit: int = Query(default=20, ge=1, le=100)):
    date = _resolve_date(date)
    start, end = day_bounds(date)
    items = _active(browser_timeline(start, end))
    totals, ids = _group_seconds(items, "site_name")
    all_seconds = sum(totals.values())
    ordered = sorted(totals.items(), key=lambda x: (-x[1], x[0]))[:limit]
    return {"data": {"date": date, "sites": [
        {"name": name, "total_seconds": seconds,
         "percentage": round(seconds / all_seconds * 100, 1) if all_seconds else 0,
         "visits": len(ids[name])}
        for name, seconds in ordered
    ]}}


@router.get("/keywords")
def get_keywords(days: int = Query(default=7, ge=1, le=90),
                 limit: int = Query(default=50, ge=1, le=200)):
    end = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    start = end - timedelta(days=days)
    words = Counter()
    seen: set[tuple[int, str]] = set()
    for item in _active(browser_timeline(start, end)):
        for word in filter(None, (x.strip() for x in item.keywords.split(","))):
            marker = (item.activity_id, word)
            if marker not in seen:
                words[word] += 1
                seen.add(marker)
    return {"data": {"days": days, "keywords": [
        {"word": word, "count": count} for word, count in words.most_common(limit)
    ]}}


def _dimension_response(date: str, attribute: str, output_key: str):
    resolved = _resolve_date(date)
    start, end = day_bounds(resolved)
    items = _active(browser_timeline(start, end) if attribute == "content_type"
                    else activity_timeline(start, end))
    totals, ids = _group_seconds(items, attribute)
    ordered = sorted(totals.items(), key=lambda x: (-x[1], x[0]))
    return {"data": {"date": resolved, output_key: [
        {"type": name, "total_seconds": seconds, "count": len(ids[name])}
        for name, seconds in ordered
    ]}}


@router.get("/content_types")
def get_content_types(date: str = Query(default="today")):
    return _dimension_response(date, "content_type", "types")


@router.get("/file_types")
def get_file_types(date: str = Query(default="today")):
    return _dimension_response(date, "file_type", "file_types")
