from __future__ import annotations

from datetime import datetime, timedelta

import pytest


class FakeClock:
    def __init__(self, now: datetime):
        self.current = now
        self.monotonic_value = 0.0

    def now(self) -> datetime:
        return self.current

    def monotonic(self) -> float:
        return self.monotonic_value

    def advance(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)
        self.monotonic_value += seconds


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock(datetime(2026, 7, 13, 10, 0, 0))

