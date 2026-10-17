"""Clocks so tests can freeze or advance time."""

from datetime import datetime, timedelta, timezone


class Clock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FrozenClock:
    """A clock that only moves when a test calls `advance`."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 10, 17, 21, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> datetime:
        self._now += timedelta(seconds=seconds)
        return self._now
