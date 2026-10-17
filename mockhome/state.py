"""Async mock-home service: lock, expiry, and state broadcasts."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import WebSocket

from fluffy.clock import Clock, SystemClock
from fluffy.config import HomeConfig
from fluffy.tools.home_state import Effect, HomeState
from fluffy.tools.registry import BoundTools, ToolArgumentError, configure


class HomeService:
    def __init__(self, home: HomeConfig, clock: Clock | None = None, bound: BoundTools | None = None) -> None:
        self.home = home
        self.clock = clock or SystemClock()
        self.bound = bound or configure(home)
        self.state = HomeState.from_config(home, now=self.clock.now())
        self.lock = asyncio.Lock()
        self.clients: dict[WebSocket, asyncio.Lock] = {}

    async def apply(self, tool: str, payload: dict[str, Any], source: str) -> dict[str, Any]:
        async with self.lock:
            args = self.bound.parse_plan_arguments(tool, payload)
            now = self.clock.now()
            self.state.note(now, source)
            effect = self.bound.apply(self.state, tool, args, now)
            message = self._message(effect, source)
        await self.broadcast(message)
        return {
            "status": "ok",
            "tool": tool,
            "source": source,
            "previous_state": effect.previous_state,
            "new_state": effect.new_state,
        }

    async def acknowledge(self, alert_id: str) -> dict[str, Any]:
        async with self.lock:
            effect = self.state.acknowledge(alert_id, self.clock.now(), "manual")
            message = self._message(effect, "manual")
        await self.broadcast(message)
        return effect.new_state["alert"]

    async def dismiss_caption(self) -> dict[str, Any]:
        async with self.lock:
            effect = self.state.dismiss_caption(self.clock.now(), "manual")
            message = self._message(effect, "manual")
        await self.broadcast(message)
        return effect.new_state

    async def doorbell(self, room: str) -> dict[str, Any]:
        async with self.lock:
            event = self.state.doorbell(room, self.clock.now(), "manual")
            message = {
                "type": "sensor.event",
                "topic": "sensor",
                "source": "manual",
                "event": "doorbell",
                "room": room,
                "at": event["at"],
                "state": self.state.snapshot(),
            }
        await self.broadcast(message)
        return event

    async def expire_due(self) -> None:
        async with self.lock:
            effects = self.state.expire(self.clock.now())
            messages = [self._message(effect, "system") for effect in effects]
        for message in messages:
            await self.broadcast(message)

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        lock = asyncio.Lock()
        self.clients[websocket] = lock
        async with self.lock:
            snapshot = self.state.snapshot()
        await self._send(
            websocket,
            {"type": "state.snapshot", "source": "snapshot", "state": snapshot, "at": snapshot["updated_at"]},
            lock,
        )

    async def disconnect(self, websocket: WebSocket) -> None:
        self.clients.pop(websocket, None)

    async def broadcast(self, message: dict[str, Any]) -> None:
        for websocket, lock in list(self.clients.items()):
            await self._send(websocket, message, lock)

    def _message(self, effect: Effect, source: str) -> dict[str, Any]:
        snapshot = self.state.snapshot()
        return {
            "type": "state",
            "topic": effect.topic,
            "source": source,
            "previous_state": effect.previous_state,
            "new_state": effect.new_state,
            "state": snapshot,
            "at": snapshot["updated_at"],
        }

    async def _send(self, websocket: WebSocket, message: dict[str, Any], lock: asyncio.Lock) -> None:
        try:
            async with lock:
                await websocket.send_json(message)
        except Exception:
            self.clients.pop(websocket, None)


def source_for(plan_id: str | None) -> str:
    if plan_id and plan_id.strip():
        return f"executor:{plan_id.strip()}"
    return "manual"


def status_for_tool_error(exc: ToolArgumentError) -> int:
    if str(exc).startswith("unknown tool:"):
        return 404
    return 422
