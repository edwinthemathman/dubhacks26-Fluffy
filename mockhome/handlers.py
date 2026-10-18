"""HTTP entry points that validate with the registry and apply the matching handler."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, WebSocket, WebSocketDisconnect

from mockhome.state import HomeService, source_for, status_for_tool_error
from fluffy.tools.registry import ToolArgumentError


def build_router(service: HomeService) -> APIRouter:
    router = APIRouter()

    @router.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/api/state")
    async def state() -> dict[str, Any]:
        async with service.lock:
            return service.state.snapshot()

    @router.post("/api/tools/{tool}")
    async def call_tool(
        tool: str,
        payload: dict[str, Any],
        x_plan_id: str | None = Header(default=None),
    ) -> dict[str, Any]:
        try:
            return await service.apply(tool, payload, source_for(x_plan_id))
        except ToolArgumentError as exc:
            raise HTTPException(status_code=status_for_tool_error(exc), detail=str(exc)) from exc

    @router.post("/api/sensors/doorbell")
    async def doorbell(request: Request) -> dict[str, Any]:
        room = await _optional_room(request)
        if room is None:
            if len(service.home.room_names) != 1:
                raise HTTPException(status_code=422, detail="room is required")
            room = service.home.room_names[0]
        try:
            return await service.doorbell(room)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc

    @router.post("/api/caregiver/ack")
    async def acknowledge(payload: dict[str, Any]) -> dict[str, Any]:
        alert_id = payload.get("alert_id")
        if not isinstance(alert_id, str) or not alert_id:
            raise HTTPException(status_code=422, detail="alert_id is required")
        try:
            return await service.acknowledge(alert_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc

    @router.post("/api/caption/dismiss")
    async def dismiss_caption() -> dict[str, Any]:
        try:
            return await service.dismiss_caption()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="no caption is showing") from exc

    @router.websocket("/ws/state")
    async def state_socket(websocket: WebSocket) -> None:
        await service.connect(websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            await service.disconnect(websocket)

    return router


async def _optional_room(request: Request) -> str | None:
    room = request.query_params.get("room")
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
        except json.JSONDecodeError:
            body = None
        if isinstance(body, dict) and isinstance(body.get("room"), str):
            room = body["room"]
    return room
