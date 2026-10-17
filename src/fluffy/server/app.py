"""Harness on port 8000.

Serves the tool manifest, plan submission, and the executor, UI, and caregiver
sockets. Device changes stay on the mock home; this process only mirrors them.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import websockets
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fluffy.clock import Clock, SystemClock
from fluffy.config import HomeConfig, load_home
from fluffy.link.executor_ws import ExecutorLink
from fluffy.models.plan import PlanProposed, PlanValidationError
from fluffy.tools.registry import BoundTools, configure

logger = logging.getLogger(__name__)


@dataclass
class Settings:
    host: str = "127.0.0.1"
    port: int = 8000
    home_url: str = "http://127.0.0.1:8100"
    config_path: Path | None = None

    @property
    def home_ws(self) -> str:
        base = self.home_url.rstrip("/")
        if base.startswith("https://"):
            return "wss://" + base.removeprefix("https://") + "/ws/state"
        if base.startswith("http://"):
            return "ws://" + base.removeprefix("http://") + "/ws/state"
        return base + "/ws/state"


def create_app(
    settings: Settings | None = None,
    *,
    home: HomeConfig | None = None,
    clock: Clock | None = None,
) -> FastAPI:
    settings = settings or Settings()
    home = home or load_home(settings.config_path)
    bound = configure(home)
    link = ExecutorLink(bound, clock or SystemClock())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        stop = asyncio.Event()
        app.state.stop = stop
        tasks = [
            asyncio.create_task(_expire_loop(link, stop), name="plan-expiry"),
            asyncio.create_task(_mirror_home(settings.home_ws, link, stop), name="home-mirror"),
        ]
        try:
            yield
        finally:
            stop.set()
            for task in tasks:
                task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title="Fluffy harness", lifespan=lifespan)
    app.state.settings = settings
    app.state.home = home
    app.state.bound = bound
    app.state.link = link

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "executor_connected": link.ready}

    @app.get("/api/tools")
    async def tools() -> dict[str, Any]:
        return bound.manifest()

    @app.post("/api/plans")
    async def create_plan(body: PlanProposed, wait: bool = True) -> dict[str, Any]:
        try:
            record = await link.submit(body)
        except PlanValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not wait:
            return record.public()
        timeout = _wait_timeout(record)
        try:
            await asyncio.wait_for(record.finished.wait(), timeout=timeout)
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail="plan was not decided") from exc
        return record.public()

    @app.get("/api/plans/{plan_id}")
    async def get_plan(plan_id: str) -> dict[str, Any]:
        record = link.records.get(plan_id)
        if record is None:
            raise HTTPException(status_code=404, detail="unknown plan")
        return record.public()

    @app.post("/api/plans/{plan_id}/cancel")
    async def cancel_plan(plan_id: str, reason: str = "cancelled") -> dict[str, Any]:
        try:
            message = await link.cancel(plan_id, reason)
        except PlanValidationError as exc:
            status = 404 if "unknown plan" in str(exc) else 409
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        return message.model_dump(mode="json")

    @app.websocket("/ws/executor")
    async def executor_socket(websocket: WebSocket) -> None:
        attached = await link.attach_executor(websocket)
        if not attached:
            return
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send_json({"type": "error", "error": "malformed JSON"})
                    continue
                if not isinstance(message, dict):
                    await websocket.send_json({"type": "error", "error": "message must be an object"})
                    continue
                await link.on_executor_message(message)
        except WebSocketDisconnect:
            pass
        finally:
            await link.detach_executor(websocket)

    @app.websocket("/ws/ui")
    async def ui_socket(websocket: WebSocket) -> None:
        await link.connect_ui(websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            await link.disconnect(websocket)

    @app.websocket("/ws/caregiver")
    async def caregiver_socket(websocket: WebSocket) -> None:
        await link.connect_caregiver(websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            await link.disconnect(websocket)

    return app


def current_tools(app: FastAPI) -> BoundTools:
    return app.state.bound


def _wait_timeout(record: Any) -> float:
    remaining = (record.proposed.plan.expires_at - record.proposed.plan.created_at).total_seconds()
    return max(remaining, 1.0) + 5.0


async def _expire_loop(link: ExecutorLink, stop: asyncio.Event) -> None:
    while not stop.is_set():
        await link.sweep()
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=0.05)


async def _mirror_home(url: str, link: ExecutorLink, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            async with websockets.connect(url) as socket:
                async for raw in socket:
                    if stop.is_set():
                        break
                    message = json.loads(raw)
                    if isinstance(message, dict):
                        await link.ingest_home(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("mock home stream unavailable: %s", exc)
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=0.3)
