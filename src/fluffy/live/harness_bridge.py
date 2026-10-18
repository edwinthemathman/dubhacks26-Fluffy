"""Host one live loop inside the harness and hand proposals to Runtime."""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
from array import array
from dataclasses import dataclass, field

from fluffy.brain.habits import load_habits
from fluffy.config import RuntimeSettings
from fluffy.ids import new_id
from fluffy.live.loop import LiveLoop, PerceptronPerceiver
from fluffy.live.perceive import Read
from fluffy.models.contracts import SCHEMA_VERSION, ActionProposal, Observation, PlanSubmission
from fluffy.profile import load_profiles
from fluffy.server.runtime import Runtime

log = logging.getLogger("fluffy.harness.live")

MAX_JPEG_BYTES = 1_500_000
MAX_PCM_BYTES = 640_000
TARGET_RATE = 16_000


@dataclass
class MediaStats:
    frames_received: int = 0
    frames_rejected: int = 0
    audio_chunks: int = 0
    transcripts: int = 0
    perceptron_calls: int = 0
    perceptron_errors: int = 0
    stale_frames: int = 0

    def public(self) -> dict[str, int]:
        return {
            "frames_received": self.frames_received,
            "frames_rejected": self.frames_rejected,
            "stale_frames": self.stale_frames,
            "audio_chunks": self.audio_chunks,
            "transcripts": self.transcripts,
            "perceptron_calls": self.perceptron_calls,
            "perceptron_errors": self.perceptron_errors,
        }


class UnavailablePerceiver:
    """No key is configured. Scene and sound reads fail without a network call."""

    async def read_scene(self, window, *, effort, profile=None) -> Read:
        del window, effort, profile
        return Read(error="Perceptron is not configured.")

    async def read_sound(self, wav) -> Read:
        del wav
        return Read(error="Perceptron is not configured.")

    async def answer(self, wav, question) -> Read:
        del wav, question
        return Read(error="Perceptron is not configured.")

    async def transcript(self, wav, profile) -> Read:
        del wav, profile
        return Read(error="Perceptron is not configured.")

    async def read_tags(self, window, *, profile=None) -> Read:
        del window, profile
        return Read(error="Perceptron is not configured.")

    async def decide(self, window, **kwargs) -> Read:
        del window, kwargs
        return Read(error="Perceptron is not configured.")


@dataclass
class HarnessPerception:
    runtime: Runtime
    settings: RuntimeSettings
    loop: LiveLoop
    stats: MediaStats = field(default_factory=MediaStats)
    client: object | None = None
    _queue: asyncio.Queue | None = None
    _consumer: asyncio.Task | None = None
    _runner: asyncio.Task | None = None
    _last_epoch: int = 0

    @classmethod
    def build(cls, runtime: Runtime, settings: RuntimeSettings, client=None) -> HarnessPerception:
        profiles = load_profiles(settings.root / "config" / "profiles")
        profile_id = "speech_cognitive" if "speech_cognitive" in profiles else next(iter(profiles))
        habits_path = settings.root / "config" / "habits.toml"
        habits = load_habits(habits_path) if habits_path.exists() else None
        holder: dict[str, HarnessPerception] = {}

        async def submit_plan(plan, turn_id: str) -> None:
            await holder["self"]._submit(plan, turn_id)

        perceiver = PerceptronPerceiver(client) if client is not None else UnavailablePerceiver()
        live = LiveLoop(
            perceiver,
            profiles,
            profile_id=profile_id,
            habits=habits,
            delegate_execution=True,
            submit_plan=submit_plan,
            on_clap=lambda: {"ok": False, "error": "clap playback stays with the executor"},
            on_volume=lambda volume: {"ok": False, "error": "volume stays with the executor"},
            on_pause=lambda: {"ok": False, "error": "pause stays with the executor"},
        )
        perception = cls(runtime=runtime, settings=settings, loop=live, client=client)
        holder["self"] = perception
        return perception

    async def start(self) -> None:
        self._queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def emit(event: dict) -> None:
            if self._queue is None:
                return
            loop.call_soon_threadsafe(self._queue.put_nowait, event)

        self.loop.emit = emit
        self._consumer = asyncio.create_task(self._consume())
        self._runner = asyncio.create_task(self.loop.run())

    async def close(self) -> None:
        for task in (self._runner, self._consumer):
            if task is not None:
                task.cancel()
        for task in (self._runner, self._consumer):
            if task is None:
                continue
            try:
                await task
            except asyncio.CancelledError:
                pass
        closer = getattr(self.client, "close", None)
        if closer is not None:
            result = closer()
            if asyncio.iscoroutine(result):
                await result

    def accept_frame(self, *, session_id: str, epoch: int, ts_ms: int, motion: float, jpeg_base64: str) -> tuple[int, dict]:
        if session_id != self.settings.session_id:
            self.stats.frames_rejected += 1
            return 400, {"error_code": "invalid_arguments", "message": "session mismatch"}
        if epoch < self._last_epoch:
            self.stats.stale_frames += 1
            self.stats.frames_rejected += 1
            return 400, {"error_code": "stale_epoch", "message": "frame epoch is older than the active capture"}
        if ts_ms < 0:
            self.stats.frames_rejected += 1
            return 400, {"error_code": "invalid_arguments", "message": "frame timestamp is invalid"}
        try:
            jpeg = base64.b64decode(jpeg_base64, validate=True)
        except (binascii.Error, ValueError):
            self.stats.frames_rejected += 1
            return 400, {"error_code": "invalid_arguments", "message": "frame is not valid base64"}
        if len(jpeg) > MAX_JPEG_BYTES or len(jpeg) < 4 or jpeg[:2] != b"\xff\xd8":
            self.stats.frames_rejected += 1
            return 400, {"error_code": "invalid_arguments", "message": "frame is not a JPEG within the size limit"}
        self._last_epoch = epoch
        self.loop.capture_epoch = epoch
        self.loop.on_frame(ts_ms, jpeg, motion)
        self.stats.frames_received += 1
        return 202, {"status": "accepted", "analyzed": False, "frames": len(self.loop.frames.frames)}

    def accept_audio(self, *, session_id: str, epoch: int, pcm16_base64: str, sample_rate: int) -> tuple[int, dict]:
        if session_id != self.settings.session_id:
            return 400, {"error_code": "invalid_arguments", "message": "session mismatch"}
        if epoch != self.loop.capture_epoch and self.loop.capture_epoch:
            self.stats.stale_frames += 1
            return 400, {"error_code": "stale_epoch", "message": "audio epoch does not match the active capture"}
        if sample_rate <= 0:
            return 400, {"error_code": "invalid_arguments", "message": "sample rate is invalid"}
        try:
            pcm = base64.b64decode(pcm16_base64, validate=True)
        except (binascii.Error, ValueError):
            return 400, {"error_code": "invalid_arguments", "message": "audio is not valid base64"}
        if len(pcm) > MAX_PCM_BYTES or len(pcm) < 2:
            return 400, {"error_code": "invalid_arguments", "message": "audio chunk is empty or too large"}
        pcm = _resample_pcm16(pcm, sample_rate, TARGET_RATE)
        self.loop.on_audio(pcm)
        self.stats.audio_chunks += 1
        return 202, {"status": "accepted", "classified": False, "sample_rate": TARGET_RATE}

    async def _consume(self) -> None:
        assert self._queue is not None
        while True:
            event = await self._queue.get()
            kind = str(event.get("type") or "status")
            if kind == "inference.status" and event.get("status") == "started":
                self.stats.perceptron_calls += 1
            if kind == "inference.error" or (kind == "inference.status" and event.get("status") == "unavailable"):
                self.stats.perceptron_errors += 1
            payload = {key: value for key, value in event.items() if key != "type" and key != "thumb"}
            if kind == "sense.hear":
                payload.pop("text", None)
                payload["text"] = payload.get("sound") or "sound"
            try:
                await self.runtime.emit(self.settings.session_id, kind, payload)
            except Exception:
                log.warning("live event %s was not emitted", kind)

    async def _submit(self, plan, turn_id: str) -> None:
        room = "living_room"
        observation = Observation(
            observation_id=new_id("obs"),
            session_id=self.settings.session_id,
            kind="video",
            captured_at=self.runtime.clock.now(),
            room=room,
            supports=["scene_change"],
            targets=[],
            summary=(plan.intent or "Visual proposal")[:240],
            source="live",
        )
        self.runtime.remember_observation(observation)
        submission = PlanSubmission(
            schema_version=SCHEMA_VERSION,
            session_id=self.settings.session_id,
            intent=(plan.intent or "Visual proposal")[:240],
            trigger={"kind": "vision", "event": "scene_change", "room": room},
            actions=[
                ActionProposal(
                    action_id=new_id("act"),
                    tool=action.tool,
                    args=dict(action.args),
                    evidence_ids=[observation.observation_id],
                )
                for action in plan.actions[:8]
            ],
            reasoning_summary=(plan.reasoning_summary or "Visual proposal")[:500],
        )
        await self.runtime.emit(
            self.settings.session_id,
            "inference.status",
            {"stage": "submit", "status": "started", "turn_id": turn_id},
        )
        result = await self.runtime.submit(submission, explicit=False)
        await self.runtime.emit(
            self.settings.session_id,
            "turn.end",
            {"turn_id": turn_id, "outcome": str(result.get("status") or "submitted")},
        )


def _resample_pcm16(pcm: bytes, source: int, target: int) -> bytes:
    chunk = array("h")
    chunk.frombytes(pcm[: len(pcm) // 2 * 2])
    if source == target or not chunk:
        return chunk.tobytes()
    step = source / target
    out = array("h")
    pos = 0.0
    while int(pos) < len(chunk):
        out.append(chunk[int(pos)])
        pos += step
    return out.tobytes()
