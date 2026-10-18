"""Clocks so tests can freeze time and the harness can read the wall clock."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


class Clock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class SystemClock(Clock):
    """Wall clock used by the executor and the mock home."""


class FrozenClock:
    """A clock that only moves when a test calls `advance`."""

    def __init__(self, start: datetime | None = None, now: datetime | None = None) -> None:
        chosen = start or now or datetime(2026, 10, 17, 21, 0, tzinfo=timezone.utc)
        if chosen.tzinfo is None:
            chosen = chosen.replace(tzinfo=timezone.utc)
        self._now = chosen

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> datetime:
        self._now += timedelta(seconds=seconds)
        return self._now
