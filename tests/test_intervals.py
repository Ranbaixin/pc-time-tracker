from datetime import datetime

from src.intervals import ActivityInterval, normalize_activity, project_browser_details


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def activity(
    row_id: int,
    process: str,
    start: str,
    end: str | None,
    *,
    source: str = "desktop",
    site: str = "",
) -> ActivityInterval:
    return ActivityInterval(
        id=row_id,
        session_id=1,
        process_name=process,
        window_title=process,
        start=dt(start),
        end=dt(end) if end else None,
        source=source,
        site_name=site,
    )


def test_sleep_wins_without_mutating_overlapping_desktop_activity():
    desktop = activity(1, "System Idle Process", "2026-07-13 02:00:00", "2026-07-13 05:00:00")
    sleep = activity(2, "[System Sleep]", "2026-07-13 02:00:00", "2026-07-13 05:00:00")

    result = normalize_activity(
        [desktop, sleep], dt("2026-07-13 00:00:00"), dt("2026-07-14 00:00:00")
    )

    assert [(x.process_name, x.duration_seconds, x.is_idle) for x in result] == [
        ("[System Sleep]", 10800, True)
    ]
    assert desktop.end == dt("2026-07-13 05:00:00")


def test_open_activity_uses_query_now_and_is_clipped_to_range():
    current = activity(1, "Code.exe", "2026-07-12 23:50:00", None)

    result = normalize_activity(
        [current],
        dt("2026-07-13 00:00:00"),
        dt("2026-07-14 00:00:00"),
        now=dt("2026-07-13 00:20:00"),
    )

    assert len(result) == 1
    assert result[0].start == dt("2026-07-13 00:00:00")
    assert result[0].duration_seconds == 1200


def test_later_desktop_record_deterministically_wins_overlap():
    old = activity(1, "Code.exe", "2026-07-13 10:00:00", "2026-07-13 10:20:00")
    new = activity(2, "chrome.exe", "2026-07-13 10:10:00", "2026-07-13 10:30:00")

    result = normalize_activity(
        [old, new], dt("2026-07-13 10:00:00"), dt("2026-07-13 10:30:00")
    )

    assert [(x.process_name, x.duration_seconds) for x in result] == [
        ("Code.exe", 600),
        ("chrome.exe", 1200),
    ]


def test_browser_details_replace_site_without_increasing_desktop_duration():
    desktop = activity(
        1,
        "chrome.exe",
        "2026-07-13 10:00:00",
        "2026-07-13 10:30:00",
        site="Title Guess",
    )
    browser = activity(
        2,
        "[Browser] github.com",
        "2026-07-13 10:05:00",
        "2026-07-13 10:20:00",
        source="browser",
        site="github.com",
    )
    normalized = normalize_activity(
        [desktop, browser], dt("2026-07-13 10:00:00"), dt("2026-07-13 10:30:00")
    )

    projected = project_browser_details(normalized, [browser])

    assert sum(x.duration_seconds for x in normalized if not x.is_idle) == 1800
    assert sum(x.duration_seconds for x in projected) == 1800
    assert [(x.site_name, x.duration_seconds) for x in projected] == [
        ("Title Guess", 300),
        ("github.com", 900),
        ("Title Guess", 600),
    ]

