"""Harness on port 8000. Frontend and inference talk only to this process."""

from __future__ import annotations

import asyncio
import secrets
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from fluffy.config import RuntimeSettings, load_settings
from fluffy.models.contracts import DeveloperRequest, Observation, PlanSubmission, PossibleFallEvent, SchemaVersionError
from fluffy.live.text_bridge import build_text_interpreter, submit_text
from fluffy.server.runtime import Runtime, build_runtime


class AnswerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str
    source: str = "button"
    evidence_ids: list[str] | None = None


class AckBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str
    session_id: str
    status: str
    client_id: str


class SpeakerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_id: str


class TextInputBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    request_id: str = Field(pattern=r"^req_[A-Za-z0-9_\-]{4,64}$")
    text: str = Field(min_length=1, max_length=500)
    epoch: int | None = None


class TranscriptBody(TextInputBody):
    captured_at: str | None = None
    final: bool = True


class ManualDimBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    room: str
    luminosity: int
    light: str | None = None


def create_harness_app(settings: RuntimeSettings | None = None, runtime: Runtime | None = None, interpreter=None) -> FastAPI:
    settings = settings or load_settings()
    runtime = runtime or build_runtime(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime.http = httpx.AsyncClient(timeout=httpx.Timeout(settings.home.speech_total_cap_s + 5))
        app.state.runtime = runtime
        await runtime.refresh_integrations()
        tasks = []
        if settings.autotick_safety:
            tasks.append(asyncio.create_task(_safety_loop(runtime)))
        tasks.append(asyncio.create_task(_home_listener(runtime, settings)))
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await runtime.http.aclose()

    app = FastAPI(title="Fluffy harness", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:3000", "http://localhost:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.runtime = runtime
    app.state.settings = settings
    app.state.text_interpreter = interpreter or build_text_interpreter(settings)
    app.state.text_inflight = set()

    def internal(authorization: str | None) -> None:
        token = (authorization or "").removeprefix("Bearer ").strip()
        if not token or not secrets.compare_digest(token, settings.internal_token):
            raise HTTPException(status_code=401, detail={"error_code": "unauthorized", "message": "internal authentication required"})

    @app.get("/api/health")
    async def health() -> dict:
        integrations = await runtime.refresh_integrations()
        return {
            "service": "harness",
            "schema_version": "2.0",
            "session_id": settings.session_id,
            "executor_online": runtime.executor_online,
            "listening": runtime.listening,
            "integrations": integrations,
        }

    @app.get("/api/session")
    def session() -> dict:
        return {
            "session_id": settings.session_id,
            "listening": runtime.listening,
            "speaker_client_id": runtime.speaker_client_id,
            "executor_online": runtime.executor_online,
        }

    @app.get("/api/tools")
    async def tools() -> dict:
        await runtime.refresh_integrations()
        return runtime.registry.manifest(runtime.enabled_tools())

    @app.get("/api/bootstrap")
    async def bootstrap() -> dict:
        try:
            state = await runtime.proxy_state()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=503, detail={"error_code": "output_unavailable", "message": "output service is unavailable"}) from exc
        return {
            "session_id": settings.session_id,
            "sequence": runtime.sequence,
            "listening": runtime.listening,
            "speaker_client_id": runtime.speaker_client_id,
            "executor_online": runtime.executor_online,
            "questions": [
                {"question_id": item.question_id, "text": item.text, "choices": item.choices}
                for item in runtime.questions.values()
                if item.status == "open"
            ],
            "state": state,
        }

    @app.get("/api/state")
    async def state() -> dict:
        try:
            return await runtime.proxy_state()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=503, detail={"error_code": "output_unavailable", "message": "output service is unavailable"}) from exc

    @app.post("/api/plans/submit")
    async def submit(body: dict, idempotency_key: str | None = Header(default=None)) -> dict:
        if idempotency_key and idempotency_key in runtime.idempotent:
            return runtime.idempotent[idempotency_key]
        try:
            if body.get("schema_version") != "2.0":
                raise SchemaVersionError(body.get("schema_version"))
            submission = PlanSubmission.model_validate(body)
        except SchemaVersionError as exc:
            raise HTTPException(status_code=400, detail={"error_code": "unsupported_schema_version", "message": str(exc)}) from exc
        # Developer labels are server-owned. A model that names the trigger "speech"
        # does not grant authority; typed and browser text set that flag in the bridge.
        explicit = submission.trigger.kind == "developer"
        result = await runtime.submit(submission, explicit=explicit)
        if idempotency_key:
            runtime.idempotent[idempotency_key] = result
        return result

    async def _accept_words(body: TextInputBody, *, explicit: bool) -> JSONResponse:
        if body.session_id != settings.session_id:
            raise HTTPException(status_code=400, detail={"error_code": "invalid_arguments", "message": "session mismatch"})
        if not explicit and not getattr(body, "final", True):
            raise HTTPException(status_code=400, detail={"error_code": "invalid_arguments", "message": "only a final transcript is accepted"})
        if body.request_id in runtime.idempotent or body.request_id in app.state.text_inflight:
            return JSONResponse(status_code=202, content={"status": "accepted", "request_id": body.request_id, "completed": False})
        app.state.text_inflight.add(body.request_id)

        async def _finish() -> None:
            try:
                await submit_text(
                    runtime,
                    request_id=body.request_id,
                    text=body.text,
                    interpreter=app.state.text_interpreter,
                    explicit=explicit,
                )
            finally:
                app.state.text_inflight.discard(body.request_id)

        asyncio.create_task(_finish())
        return JSONResponse(status_code=202, content={"status": "accepted", "request_id": body.request_id, "completed": False})

    @app.post("/api/input/text")
    async def input_text(body: TextInputBody) -> JSONResponse:
        return await _accept_words(body, explicit=True)

    @app.post("/api/input/transcript")
    async def input_transcript(body: TranscriptBody) -> JSONResponse:
        return await _accept_words(body, explicit=False)

    @app.post("/api/dev/request")
    async def developer(body: DeveloperRequest, idempotency_key: str | None = Header(default=None)) -> dict:
        return await runtime.submit_developer(body, idempotency_key)

    @app.post("/api/observations")
    async def observe(body: Observation) -> dict:
        if body.source == "developer":
            raise HTTPException(status_code=400, detail={"error_code": "invalid_arguments", "message": "developer observations are created by the developer endpoint"})
        if body.session_id != settings.session_id:
            raise HTTPException(status_code=400, detail={"error_code": "invalid_arguments", "message": "session mismatch"})
        runtime.remember_observation(body)
        return {"observation_id": body.observation_id}

    @app.post("/api/questions/{question_id}/answer")
    async def answer(question_id: str, body: AnswerBody) -> dict:
        if body.source not in {"button", "model"}:
            raise HTTPException(status_code=400, detail={"error_code": "invalid_arguments", "message": "answer source must be button or model"})
        if body.answer in {"im_okay", "need_help", "okay", "help"} and runtime.safety and runtime.safety.incident:
            incident = runtime.safety.incident
            if incident.question_id == question_id:
                commands = runtime.safety.answer(question_id, None, body.answer, runtime.clock.now())
                await runtime.apply_safety(commands)
                return {"status": "safety", "phase": runtime.safety.incident.phase if runtime.safety.incident else None}
        return await runtime.resolve_question(question_id, body.answer, body.evidence_ids, body.source)

    @app.post("/api/incidents/possible-fall")
    async def fall(body: PossibleFallEvent) -> dict:
        if body.source == "developer":
            raise HTTPException(status_code=400, detail={"error_code": "invalid_arguments", "message": "use the labeled developer trigger"})
        return await runtime.open_fall(body)

    @app.post("/api/dev/incidents/possible-fall")
    async def dev_fall(body: PossibleFallEvent) -> dict:
        labeled = body.model_copy(update={"source": "developer"})
        return await runtime.open_fall(labeled)

    @app.post("/api/help")
    async def help_button() -> dict:
        return await runtime.help_request("button")

    @app.post("/api/sensing/pause")
    async def pause() -> dict:
        runtime.listening = "paused"
        message = runtime.safety.pause_sensing() if runtime.safety else "monitoring has stopped"
        await runtime.sync_incident()
        await runtime.emit(settings.session_id, "listening.state", {"state": "paused", "detail": message})
        return {"listening": "paused", "detail": message}

    @app.post("/api/session/stop")
    async def stop_session() -> dict:
        runtime.stopped = True
        runtime.listening = "paused"
        await runtime.emit(settings.session_id, "listening.state", {"state": "paused", "detail": "stopped"})
        return {"stopped": True, "listening": "paused"}

    @app.post("/api/sensing/resume")
    async def resume() -> dict:
        runtime.stopped = False
        runtime.listening = "live"
        await runtime.emit(settings.session_id, "listening.state", {"state": "live"})
        return {"listening": "live"}

    @app.post("/api/session/speaker")
    async def speaker(body: SpeakerBody) -> dict:
        runtime.speaker_client_id = body.client_id
        runtime.listening = "live"
        await runtime.emit(settings.session_id, "listening.state", {"state": "live", "speaker_client_id": body.client_id})
        return {"speaker_client_id": body.client_id}

    @app.post("/api/audio/ack")
    async def audio_ack(body: AckBody) -> dict:
        if runtime.speaker_client_id and body.client_id != runtime.speaker_client_id:
            raise HTTPException(status_code=409, detail={"error_code": "speaker_conflict", "message": "another client owns playback"})
        if body.status not in {"started", "ended", "failed", "blocked"}:
            raise HTTPException(status_code=400, detail={"error_code": "invalid_arguments", "message": "unknown playback status"})
        response = await runtime.http.post(
            f"{settings.home_url}/internal/playback-ack",
            json={"message_id": body.message_id, "status": body.status},
            headers=runtime.headers(),
        )
        if body.status == "started":
            runtime.listening = "suppressed"
            await runtime.emit(body.session_id, "listening.state", {"state": "suppressed", "message_id": body.message_id, "tail_ms": settings.home.suppression_tail_ms})
        elif body.status in {"ended", "failed", "blocked"}:
            if runtime.safety:
                if body.status == "ended":
                    runtime.safety.on_playback_ended(body.message_id, runtime.clock.now())
                else:
                    runtime.safety.on_audio_unavailable(body.message_id, runtime.clock.now())
            runtime.listening = "live"
            await runtime.emit(
                body.session_id,
                "listening.state",
                {"state": "live", "message_id": body.message_id, "playback": body.status, "tail_ms": settings.home.suppression_tail_ms},
            )
        return response.json()

    @app.post("/api/captions/{caption_id}/dismiss")
    async def dismiss(caption_id: str) -> dict:
        response = await runtime.http.post(
            f"{settings.home_url}/internal/captions/{caption_id}/dismiss",
            headers=runtime.headers(),
        )
        return response.json()

    @app.post("/api/manual/dim_lights")
    async def manual_dim(body: ManualDimBody) -> dict:
        payload = {"room": body.room, "luminosity": body.luminosity}
        if body.light:
            payload["light"] = body.light
        response = await runtime.http.post(
            f"{settings.home_url}/api/tools/dim_lights",
            json=payload,
            headers={
                **runtime.headers(),
                "X-Plan-Id": "pln_manual",
                "X-Action-Id": "act_manual",
                "X-Session-Id": settings.session_id,
            },
        )
        runtime.note_manual(body.light or body.room)
        return response.json()

    @app.get("/api/audio/{audio_id}")
    async def audio(audio_id: str) -> Response:
        response = await runtime.http.get(f"{settings.home_url}/api/audio/{audio_id}", headers=runtime.headers())
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail={"error_code": "not_found", "message": "audio unavailable"})
        return Response(content=response.content, media_type=response.headers.get("content-type", "audio/mpeg"))

    @app.get("/api/integrations")
    async def integrations() -> dict:
        return {"integrations": await runtime.refresh_integrations()}

    @app.get("/api/integrations/spotify/devices")
    async def devices() -> dict:
        response = await runtime.http.get(f"{settings.home_url}/api/integrations/spotify/devices", headers=runtime.headers())
        if response.status_code >= 400:
            raise HTTPException(status_code=response.status_code, detail=response.json().get("detail", response.text))
        return response.json()

    @app.post("/api/integrations/spotify/device")
    async def select_device(body: dict) -> dict:
        response = await runtime.http.post(
            f"{settings.home_url}/api/integrations/spotify/device",
            json={"device_id": body.get("device_id", "")},
            headers=runtime.headers(),
        )
        if response.status_code >= 400:
            raise HTTPException(status_code=response.status_code, detail=response.json().get("detail", response.text))
        await runtime.refresh_integrations()
        return response.json()

    @app.get("/internal/authorizations/{authorization_id}")
    def authorization(authorization_id: str, authorization: str | None = Header(default=None)) -> dict:
        internal(authorization)
        record = runtime.authorizations.get(authorization_id, runtime.clock.now())
        if record is None:
            raise HTTPException(status_code=404, detail={"error_code": "authorization_missing", "message": "authorization is missing or expired"})
        return record.model_dump(mode="json")

    @app.post("/internal/incidents/{incident_id}/claim")
    def claim(incident_id: str, authorization: str | None = Header(default=None)) -> dict:
        internal(authorization)
        return {"eligible": runtime.claim_incident(incident_id)}

    @app.websocket("/ws/ui")
    async def ws_ui(socket: WebSocket) -> None:
        await socket.accept()
        runtime.ui_clients.add(socket)
        await socket.send_json(
            {
                "event_id": "evt_hello",
                "session_id": settings.session_id,
                "sequence": runtime.sequence,
                "timestamp": runtime.clock.now().isoformat(),
                "type": "session.hello",
                "payload": {"executor_online": runtime.executor_online, "listening": runtime.listening},
            }
        )
        try:
            while True:
                await socket.receive_text()
        except WebSocketDisconnect:
            runtime.ui_clients.discard(socket)

    @app.websocket("/ws/executor")
    async def ws_executor(socket: WebSocket) -> None:
        header = socket.headers.get("authorization")
        token = (header or "").removeprefix("Bearer ").strip()
        if not token or not secrets.compare_digest(token, settings.internal_token):
            await socket.close(code=4401)
            return
        await socket.accept()
        try:
            hello = await socket.receive_json()
        except WebSocketDisconnect:
            return
        if hello.get("type") != "executor.hello" or hello.get("schema_version") != "2.0":
            await socket.send_json({"type": "error", "error_code": "unsupported_schema_version"})
            await socket.close(code=4400)
            return
        await runtime.executor_hello(socket)
        try:
            while True:
                message = await socket.receive_json()
                await runtime.handle_executor_message(message)
        except WebSocketDisconnect:
            if runtime.executor is socket:
                runtime.executor = None
                runtime.executor_online = False
                await runtime.emit(settings.session_id, "executor.status", {"online": False})

    return app


async def _safety_loop(runtime: Runtime) -> None:
    while True:
        try:
            await runtime.safety_tick()
        except Exception:
            pass
        await runtime.clock.sleep(0.2)


async def _home_listener(runtime: Runtime, settings: RuntimeSettings) -> None:
    import json

    import websockets

    url = settings.home_url.replace("http://", "ws://") + "/ws/state"
    while True:
        try:
            async with websockets.connect(url, additional_headers=runtime.headers()) as socket:
                async for raw in socket:
                    message = json.loads(raw)
                    payload = message.get("payload") or {}
                    await runtime.emit(settings.session_id, "home.snapshot", payload)
                    for media in payload.get("media") or []:
                        if media.get("playback") == "playing" and media.get("sound_id"):
                            runtime.playing_sound[media["room"]] = media["sound_id"]
                        elif media.get("playback") in {"paused", "idle", "error"}:
                            runtime.playing_sound.pop(media.get("room"), None)
        except Exception:
            await asyncio.sleep(0.5)


def main() -> None:
    import uvicorn

    settings = load_settings()
    uvicorn.run(create_harness_app(settings), host=settings.harness_host, port=settings.harness_port)


if __name__ == "__main__":
    main()
