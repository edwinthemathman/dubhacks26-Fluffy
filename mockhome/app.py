"""Mock home API on port 8100."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from fluffy.clock import Clock, SystemClock
from fluffy.config import HomeConfig, load_home
from mockhome.handlers import build_router
from mockhome.state import HomeService


def create_app(home: HomeConfig | None = None, clock: Clock | None = None) -> FastAPI:
    home = home or load_home()
    service = HomeService(home, clock)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        stop = asyncio.Event()
        task = asyncio.create_task(_expire_loop(service, stop), name="mockhome-expiry")
        try:
            yield
        finally:
            stop.set()
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="Fluffy mock home", lifespan=lifespan)
    app.state.service = service
    app.include_router(build_router(service))
    return app


async def _expire_loop(service: HomeService, stop: asyncio.Event) -> None:
    while not stop.is_set():
        await service.expire_due()
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=0.05)
