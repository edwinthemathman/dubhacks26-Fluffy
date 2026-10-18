"""Clock abstraction so tests can freeze time."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


class Clock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    async def sleep(self, seconds: float) -> None:
        import asyncio

        await asyncio.sleep(seconds)


class SystemClock(Clock):
    """Wall clock. Kept as a name inference and older tests already use."""


class FakeClock(Clock):
    def __init__(self, start: datetime | None = None, now: datetime | None = None) -> None:
        chosen = start or now or datetime(2026, 10, 18, 1, 0, tzinfo=timezone.utc)
        if chosen.tzinfo is None:
            chosen = chosen.replace(tzinfo=timezone.utc)
        self._now = chosen

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> datetime:
        self._now = self._now + timedelta(seconds=seconds)
        return self._now

    async def sleep(self, seconds: float) -> None:
        self.advance(seconds)


class FrozenClock(FakeClock):
    """Name used by the inference tests. Same behavior as FakeClock."""
