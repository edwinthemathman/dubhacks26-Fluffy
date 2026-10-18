"""Live harness server on :8000. The web app (Vite on :5173) proxies /ws and /api here.

  /ws/live  binary in from the browser:
            frame:  [1][uint64 ts_ms][float32 motion][jpeg bytes]   (little-endian)
            audio:  [2][pcm16 mono 16 kHz bytes]
  /ws/ui    FluffyEvents out to every screen; ClientMessages in (answers, typed text, profile, reset)

Run from the repo root: `uv run fluffy-live`
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import struct
from pathlib import Path
from typing import Any

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from fluffy.brain.habits import load_habits
from fluffy.live.loop import LiveLoop, PerceptronPerceiver
from fluffy.profile import load_profiles
from fluffy.voice.elevenlabs import SAFETY_LINES, ElevenLabsVoice

ROOT = Path(__file__).resolve().parents[3]
FRAME = 1
AUDIO = 2
FRAME_HEADER = struct.Struct("<BQf")

log = logging.getLogger("fluffy.live")


class PhotonReply(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    sender: str = Field(alias="from")
    text: str


class Hub:
    """Fans events out to every connected screen."""

    def __init__(self) -> None:
        self.screens: set[WebSocket] = set()
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    def emit(self, event: dict[str, Any]) -> None:
        self.queue.put_nowait(event)

    async def pump(self) -> None:
        while True:
            event = await self.queue.get()
            text = json.dumps(event, default=str)
            for screen in list(self.screens):
                try:
                    await screen.send_text(text)
                except Exception:
                    self.screens.discard(screen)


def create_app(loop_factory=None, voice_factory=None) -> FastAPI:
    hub = Hub()
    state: dict[str, Any] = {}
    feeds = 0

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        if loop_factory is not None:
            live = loop_factory(hub.emit)
            client = None
            voice = voice_factory() if voice_factory is not None else ElevenLabsVoice("")
        else:
            from perceptron import AsyncClient

            load_dotenv(ROOT / ".env")
            key = os.environ.get("PERCEPTRON_API_KEY", "").strip()
            if not key:
                raise RuntimeError("PERCEPTRON_API_KEY is not set (put it in .env).")
            client = AsyncClient(api_key=key, provider="perceptron")
            profiles = load_profiles(ROOT / "config" / "profiles")
            habits = load_habits(ROOT / "config" / "habits.toml")
            live = LiveLoop(
                PerceptronPerceiver(client),
                profiles,
                profile_id="blind",
                emit=hub.emit,
                habits=habits,
            )
            voice = ElevenLabsVoice.from_env()
            await voice.pregenerate(SAFETY_LINES, (1.0, 0.85))
        state["loop"] = live
        state["voice"] = voice
        tasks = [asyncio.create_task(hub.pump()), asyncio.create_task(live.run())]
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            if client is not None:
                await client.aclose()
            await voice.aclose()

    app = FastAPI(title="Fluffy live", lifespan=lifespan)

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        live: LiveLoop = state["loop"]
        voice: ElevenLabsVoice = state["voice"]
        return {
            "ok": True,
            "frames": len(live.frames.frames),
            "profile": live.profile.id,
            "paused": live.paused,
            "voice": voice.available,
            "timings": live.timings.as_dict(),
        }

    @app.post("/api/integrations/photon/reply")
    def photon_reply(body: PhotonReply) -> dict[str, bool]:
        live: LiveLoop = state["loop"]
        live.on_photon_reply(body.sender, body.text)
        return {"ok": True}

    @app.get("/api/voice/{say_id}")
    async def voice(say_id: str):
        live: LiveLoop = state["loop"]
        speaker: ElevenLabsVoice = state["voice"]
        record = live.says.get(say_id)
        if record is None or not speaker.available:
            return Response(status_code=204)
        cached = speaker.cached(record["text"], record["speed"])
        if cached is not None:
            return Response(content=cached, media_type="audio/mpeg")

        agen = speaker.stream(record["text"], record["speed"])
        try:
            first = await agen.__anext__()
        except StopAsyncIteration:
            return Response(status_code=204)

        async def body():
            yield first
            async for chunk in agen:
                yield chunk

        return StreamingResponse(body(), media_type="audio/mpeg")

    @app.websocket("/ws/ui")
    async def ui(socket: WebSocket) -> None:
        await socket.accept()
        live: LiveLoop = state["loop"]
        for event in live.hello():
            await socket.send_text(json.dumps(event, default=str))
        hub.screens.add(socket)
        try:
            while True:
                text = await socket.receive_text()
                try:
                    message = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(message, dict):
                    await live.on_client(message)
        except WebSocketDisconnect:
            pass
        finally:
            hub.screens.discard(socket)

    @app.websocket("/ws/live")
    async def feed(socket: WebSocket) -> None:
        nonlocal feeds
        await socket.accept()
        live: LiveLoop = state["loop"]
        feeds += 1
        live.paused = False
        try:
            while True:
                message = await socket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                data = message.get("bytes")
                if data:
                    handle_binary(live, data)
                elif message.get("text"):
                    with contextlib.suppress(json.JSONDecodeError):
                        await live.on_client(json.loads(message["text"]))
        except WebSocketDisconnect:
            pass
        finally:
            feeds -= 1
            if feeds == 0:
                live.on_feed_closed()

    return app


def handle_binary(live: LiveLoop, data: bytes) -> None:
    kind = data[0]
    if kind == FRAME and len(data) > FRAME_HEADER.size:
        _, ts_ms, motion = FRAME_HEADER.unpack_from(data)
        live.on_frame(int(ts_ms), data[FRAME_HEADER.size :], float(motion))
    elif kind == AUDIO and len(data) > 1:
        live.on_audio(data[1:])


app = create_app()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    uvicorn.run("fluffy.live.server:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
