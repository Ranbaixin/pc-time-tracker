"""Non-destructive activity interval normalization and browser enrichment."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Iterable, Mapping

logger = logging.getLogger(__name__)

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
SLEEP_PROCESSES = {"[System Sleep]"}
IDLE_PROCESSES = {"[System Idle]", "System Idle Process"}
BROWSER_PROCESSES = {
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "browser.exe"
}


@dataclass(frozen=True)
class ActivityInterval:
    id: int
    session_id: int
    process_name: str
    window_title: str
    start: datetime
    end: datetime | None
    source: str = "desktop"
    process_path: str = ""
    category: str = ""
    sub_category: str = ""
    site_name: str = ""
    project_name: str = ""
    file_type: str = ""
    content_type: str = ""
    keywords: str = ""


@dataclass(frozen=True)
class NormalizedSlice:
    activity_id: int
    session_id: int
    process_name: str
    window_title: str
    start: datetime
    end: datetime
    source: str
    process_path: str = ""
    category: str = ""
    sub_category: str = ""
    site_name: str = ""
    project_name: str = ""
    file_type: str = ""
    content_type: str = ""
    keywords: str = ""

    @property
    def duration_seconds(self) -> int:
        return max(0, int((self.end - self.start).total_seconds()))

    @property
    def is_idle(self) -> bool:
        return self.process_name in SLEEP_PROCESSES | IDLE_PROCESSES


def parse_db_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, TIME_FORMAT)
    except (TypeError, ValueError):
        logger.warning("invalid_activity_timestamp", extra={"value": value})
        return None


def interval_from_row(row: Mapping) -> ActivityInterval | None:
    data = dict(row)
    start = parse_db_time(data.get("start_time"))
    if start is None:
        return None
    end_raw = data.get("end_time")
    end = parse_db_time(end_raw) if end_raw else None
    if end_raw and end is None:
        return None
    return ActivityInterval(
        id=int(data.get("id", 0)),
        session_id=int(data.get("session_id", 0)),
        process_name=data.get("process_name") or "",
        window_title=data.get("window_title") or "",
        start=start,
        end=end,
        source=data.get("source") or "desktop",
        process_path=data.get("process_path") or "",
        category=data.get("category") or "",
        sub_category=data.get("sub_category") or "",
        site_name=data.get("site_name") or "",
        project_name=data.get("project_name") or "",
        file_type=data.get("file_type") or "",
        content_type=data.get("content_type") or "",
        keywords=data.get("keywords") or "",
    )


def _priority(item: ActivityInterval) -> tuple[int, datetime, int]:
    if item.process_name in SLEEP_PROCESSES:
        rank = 30
    elif item.process_name in IDLE_PROCESSES:
        rank = 20
    else:
        rank = 10
    return rank, item.start, item.id


def _to_slice(item: ActivityInterval, start: datetime, end: datetime) -> NormalizedSlice:
    return NormalizedSlice(
        activity_id=item.id,
        session_id=item.session_id,
        process_name=item.process_name,
        window_title=item.window_title,
        start=start,
        end=end,
        source="system" if item.process_name in SLEEP_PROCESSES | IDLE_PROCESSES else item.source,
        process_path=item.process_path,
        category=item.category,
        sub_category=item.sub_category,
        site_name=item.site_name,
        project_name=item.project_name,
        file_type=item.file_type,
        content_type=item.content_type,
        keywords=item.keywords,
    )


def _merge_adjacent(items: list[NormalizedSlice]) -> list[NormalizedSlice]:
    merged: list[NormalizedSlice] = []
    for item in items:
        if (
            merged
            and replace(merged[-1], start=item.start, end=item.end) == item
            and merged[-1].end == item.start
        ):
            merged[-1] = replace(merged[-1], end=item.end)
        else:
            merged.append(item)
    return merged


def normalize_activity(
    intervals: Iterable[ActivityInterval],
    range_start: datetime,
    range_end: datetime,
    *,
    now: datetime | None = None,
) -> list[NormalizedSlice]:
    """Return a deterministic, non-overlapping desktop/system timeline."""
    effective_now = now or datetime.now()
    candidates: list[ActivityInterval] = []
    for item in intervals:
        if item.source == "browser":
            continue
        end = item.end or effective_now
        if end <= item.start or end <= range_start or item.start >= range_end:
            continue
        candidates.append(replace(item, end=end))

    boundaries = {range_start, range_end}
    for item in candidates:
        boundaries.add(max(range_start, item.start))
        boundaries.add(min(range_end, item.end or effective_now))
    ordered = sorted(boundaries)
    result: list[NormalizedSlice] = []
    for start, end in zip(ordered, ordered[1:], strict=False):
        if start >= end:
            continue
        active = [x for x in candidates if x.start < end and (x.end or effective_now) > start]
        if not active:
            continue
        winner = max(active, key=_priority)
        result.append(_to_slice(winner, start, end))
    return _merge_adjacent(result)


def _is_browser(slice_: NormalizedSlice) -> bool:
    return slice_.process_name in BROWSER_PROCESSES or slice_.sub_category == "浏览器"


def project_browser_details(
    desktop_slices: Iterable[NormalizedSlice], browser_intervals: Iterable[ActivityInterval]
) -> list[NormalizedSlice]:
    """Overlay browser metadata onto authoritative desktop browser time."""
    browsers = [x for x in browser_intervals if x.source == "browser" and x.end and x.end > x.start]
    result: list[NormalizedSlice] = []
    for base in desktop_slices:
        if base.is_idle or not _is_browser(base):
            result.append(base)
            continue
        relevant = [x for x in browsers if x.start < base.end and x.end and x.end > base.start]
        boundaries = {base.start, base.end}
        for item in relevant:
            boundaries.add(max(base.start, item.start))
            boundaries.add(min(base.end, item.end or base.end))
        ordered = sorted(boundaries)
        for start, end in zip(ordered, ordered[1:], strict=False):
            active = [x for x in relevant if x.start < end and x.end and x.end > start]
            if not active:
                result.append(replace(base, start=start, end=end))
                continue
            winner = max(active, key=lambda x: (x.start, x.id))
            result.append(
                replace(
                    base,
                    start=start,
                    end=end,
                    site_name=winner.site_name,
                    content_type=winner.content_type,
                    keywords=winner.keywords,
                    process_path=winner.process_path,
                )
            )
    return _merge_adjacent(result)
