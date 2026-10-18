"""Output service HTTP API. Tool routes accept only internal callers."""

from __future__ import annotations

import asyncio
import secrets
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict

from fluffy.config import RuntimeSettings
from fluffy.models.contracts import IncidentView
from fluffy.tools.registry import build_registry
from mockhome.state import HomeStore, build_store


class AckBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str
    status: str


class DeviceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail={"error_code": "unauthorized", "message": "internal authentication required"})


def create_home_app(settings: RuntimeSettings, store: HomeStore | None = None) -> FastAPI:
    registry = build_registry(settings.home)
    home = store or build_store(
        settings.home,
        registry,
        settings.integrations,
        settings.data_dir,
        settings.root,
        lambda: datetime.now(timezone.utc),
    )
    app = FastAPI(title="Fluffy output service")
    app.state.store = home
    app.state.settings = settings

    def authorized(header: str | None) -> None:
        token = (header or "").removeprefix("Bearer ").strip()
        if not token or not secrets.compare_digest(token, settings.internal_token):
            raise _unauthorized()

    @app.get("/api/health")
    def health() -> dict:
        spotify = home.spotify.readiness()
        speech = home.speech.readiness_detail()
        photon = home.photon.readiness()
        return {
            "service": "output",
            "integrations": [
                {"provider": "spotify", "readiness": spotify[0], "detail": spotify[1]},
                {"provider": "elevenlabs", "readiness": speech[0], "detail": speech[1]},
                {"provider": "photon", "readiness": photon[0], "detail": photon[1]},
            ],
        }

    @app.get("/api/state")
    def state(authorization: str | None = Header(default=None)) -> dict:
        authorized(authorization)
        return home.snapshot().model_dump(mode="json")

    @app.post("/api/tools/{tool}")
    def call_tool(
        tool: str,
        payload: dict,
        authorization: str | None = Header(default=None),
        x_plan_id: str | None = Header(default=None),
        x_action_id: str | None = Header(default=None),
        x_session_id: str | None = Header(default=None),
    ) -> dict:
        authorized(authorization)
        if not x_plan_id or not x_action_id or not x_session_id:
            raise HTTPException(status_code=400, detail={"error_code": "invalid_arguments", "message": "plan, action, and session headers are required"})
        effect = home.apply(tool, payload, x_session_id, x_plan_id, x_action_id)
        return effect.model_dump(mode="json")

    @app.get("/api/audio/{audio_id}")
    def audio(audio_id: str, authorization: str | None = Header(default=None)) -> Response:
        authorized(authorization)
        found = home.audio_bytes(audio_id)
        if found is None:
            raise HTTPException(status_code=404, detail={"error_code": "not_found", "message": "audio expired or unknown"})
        body, content_type = found
        return Response(content=body, media_type=content_type)

    @app.post("/internal/playback-ack")
    def playback_ack(body: AckBody, authorization: str | None = Header(default=None)) -> dict:
        authorized(authorization)
        message = home.playback_ack(body.message_id, body.status)
        if message is None:
            raise HTTPException(status_code=404, detail={"error_code": "not_found", "message": "unknown message"})
        return message.model_dump(mode="json")

    @app.post("/internal/cancel-audio")
    def cancel_audio(authorization: str | None = Header(default=None)) -> dict:
        authorized(authorization)
        home.cancel_local_audio()
        return {"status": "cancelled"}

    @app.post("/internal/captions/{caption_id}/dismiss")
    def dismiss(caption_id: str, authorization: str | None = Header(default=None)) -> dict:
        authorized(authorization)
        return {"dismissed": home.dismiss_caption(caption_id)}

    @app.post("/internal/incident")
    def incident(body: IncidentView | None, authorization: str | None = Header(default=None)) -> dict:
        authorized(authorization)
        home.set_incident(body)
        return {"ok": True}

    @app.get("/api/integrations/spotify/connect")
    def spotify_connect() -> RedirectResponse:
        try:
            url = home.spotify.begin_oauth(datetime.now(timezone.utc))
        except Exception as exc:
            code = getattr(exc, "error_code", "spotify_not_configured")
            raise HTTPException(status_code=400, detail={"error_code": code, "message": str(exc)}) from exc
        return RedirectResponse(url)

    @app.get("/api/integrations/spotify/callback")
    def spotify_callback(code: str = "", state: str = "", error: str = "") -> HTMLResponse:
        if error or not code or not state:
            return HTMLResponse("<p>Spotify authorization was not completed.</p>", status_code=400)
        try:
            home.spotify.exchange_code(code, state, datetime.now(timezone.utc))
        except Exception:
            return HTMLResponse("<p>Spotify authorization failed. You can close this tab and try again.</p>", status_code=400)
        return HTMLResponse("<p>Spotify connected. You can close this tab and select the demo device in Fluffy.</p>")

    @app.get("/api/integrations/spotify/devices")
    def devices(authorization: str | None = Header(default=None)) -> dict:
        authorized(authorization)
        try:
            found = home.spotify.list_devices()
        except Exception as exc:
            code = getattr(exc, "error_code", "spotify_connection_error")
            raise HTTPException(status_code=400, detail={"error_code": code, "message": str(exc)}) from exc
        return {
            "devices": [
                {"id": item.id, "name": item.name, "is_active": item.is_active, "supports_volume": item.supports_volume}
                for item in found
            ],
            "selected_device_id": home.spotify.state.selected_device_id,
        }

    @app.post("/api/integrations/spotify/device")
    def select_device(body: DeviceBody, authorization: str | None = Header(default=None)) -> dict:
        authorized(authorization)
        try:
            device = home.spotify.select_device(body.device_id)
        except Exception as exc:
            code = getattr(exc, "error_code", "spotify_no_device")
            raise HTTPException(status_code=400, detail={"error_code": code, "message": str(exc)}) from exc
        return {"id": device.id, "name": device.name, "supports_volume": device.supports_volume}

    @app.websocket("/ws/state")
    async def ws_state(socket: WebSocket) -> None:
        header = socket.headers.get("authorization")
        token = (header or "").removeprefix("Bearer ").strip()
        if not token or not secrets.compare_digest(token, settings.internal_token):
            await socket.close(code=4401)
            return
        await socket.accept()
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def listener(snapshot) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, snapshot.model_dump(mode="json"))

        home.subscribe(listener)
        await socket.send_json({"type": "home.snapshot", "payload": home.snapshot().model_dump(mode="json")})
        try:
            while True:
                payload = await queue.get()
                await socket.send_json({"type": "home.snapshot", "payload": payload})
        except WebSocketDisconnect:
            return

    return app


def main() -> None:
    import uvicorn

    from fluffy.config import load_settings

    settings = load_settings()
    uvicorn.run(create_home_app(settings), host=settings.home_host, port=settings.home_port)


if __name__ == "__main__":
    main()
