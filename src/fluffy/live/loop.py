"""The live scheduler: camera and mic in, Perceptron reads out, tool calls to the room.

Lanes (at most one call in flight each):
  watch   motion then settle, a long stretch of motion, or a heartbeat -> scene read
  hear    the end of a burst of sound -> sound read (or a yes/no read while a question is open)
  decide  a new cue or speech -> decision call with tools; a newer trigger replaces a queued
          one and results for a trigger older than `stale_s` are dropped
  safety  a fall or a call for help starts the check-in at once, without waiting on the model

Everything that happens is emitted as the web app's `FluffyEvent`s (web/src/stream/events.ts).
"""

from __future__ import annotations

import asyncio
import base64
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from fluffy.brain.consent import ConsentManager, ConsentUpdate
from fluffy.brain.cues import NOTIFY_SOUNDS, derive
from fluffy.brain.decide import _plan_from_devices
from fluffy.brain.safety import CHECKIN_TEXT, SafetyEffect, SafetyMachine
from fluffy.clock import Clock
from fluffy.ids import IdFactory
from fluffy.live.buffers import AudioBuffer, FrameBuffer, SpeechGate, to_wav
from fluffy.live.home import HomeState
from fluffy.live.perceive import Decision, Read, Window
from fluffy.models import Plan, Trigger
from fluffy.perception.scene import SceneState, SoundRead, apply_sound_read
from fluffy.profile.model import Profile

log = logging.getLogger("fluffy.live")

Emit = Callable[[dict[str, Any]], None]

SAFETY_UI = {"checkin": "checkin1", "checkin_repeat": "checkin2", "alerted": "alerted"}


@dataclass
class LiveSettings:
    # Defaults come from docs/live-spike.md: a 6-8 frame read with no reasoning took about 1 s.
    watch_span_s: float = 4.0
    watch_frames: int = 8
    watch_effort: str = "none"
    heartbeat_s: float = 5.0
    motion_threshold: float = 0.04
    settle_s: float = 0.5
    max_motion_wait_s: float = 3.0
    decide_span_s: float = 6.0
    decide_frames: int = 8
    decide_effort: str = "low"
    stale_s: float = 10.0
    latch_s: float = 30.0
    budget_per_min: int = 40
    caption_s: float = 15.0


class Perceiver(Protocol):
    async def read_scene(self, window: Window, *, effort: str, profile: Profile | None = None) -> Read: ...
    async def read_sound(self, wav: bytes) -> Read: ...
    async def answer(self, wav: bytes, question: str) -> Read: ...
    async def decide(
        self,
        window: Window,
        *,
        profile: Profile,
        utterance: str,
        scene: SceneState | None,
        cues: set[str],
        effort: str,
    ) -> Decision: ...


class PerceptronPerceiver:
    """The real thing: one shared AsyncClient."""

    def __init__(self, client) -> None:
        from fluffy.live import perceive

        self._p = perceive
        self.client = client

    async def read_scene(self, window, *, effort, profile=None):
        return await self._p.read_scene(self.client, window, effort=effort, profile=profile)

    async def read_sound(self, wav):
        return await self._p.read_sound(self.client, wav)

    async def answer(self, wav, question):
        return await self._p.answer_from_audio(self.client, wav, question)

    async def decide(self, window, *, profile, utterance, scene, cues, effort):
        return await self._p.decide(
            self.client, window, profile=profile, utterance=utterance, scene=scene, cues=cues, effort=effort
        )


@dataclass
class DecideRequest:
    kind: str  # spoken | sound | offer | safety (turn kinds in the UI)
    trigger: str
    cues: set[str]
    at: float
    wav: bytes | None = None
    utterance: str = ""


@dataclass
class OpenTurn:
    """A plan waiting on the person's answer, and where its rows are on screen."""

    turn_id: str
    call_ids: dict[str, str] = field(default_factory=dict)  # action_id -> call_id


class LiveLoop:
    def __init__(
        self,
        perceiver: Perceiver,
        profiles: dict[str, Profile],
        *,
        profile_id: str = "blind",
        settings: LiveSettings | None = None,
        clock: Clock | None = None,
        emit: Emit | None = None,
    ) -> None:
        self.perceiver = perceiver
        self.profiles = profiles
        self.profile = profiles[profile_id]
        self.s = settings or LiveSettings()
        self.clock = clock or Clock()
        self.emit: Emit = emit or (lambda event: None)
        self.ids = IdFactory()
        self.frames = FrameBuffer()
        self.audio = AudioBuffer()
        self.gate = SpeechGate()
        self.home = HomeState()
        self.consent = ConsentManager(self.clock)
        self.safety = SafetyMachine(self.clock, self.profile)
        self.scene: SceneState | None = None
        self.active_cues: set[str] = set()
        self.latched: dict[str, float] = {}
        self.calls: deque[float] = deque()
        self.motion_started: float | None = None
        self.last_motion: float | None = None
        self.last_watch = 0.0
        self.pending: DecideRequest | None = None
        self.tasks: dict[str, asyncio.Task | None] = {"watch": None, "hear": None, "decide": None}
        self.open_turn: OpenTurn | None = None
        self.safety_turn: str | None = None
        self.paused = False
        self.timers: set[asyncio.Task] = set()

    # ---------- inputs ----------

    def now(self) -> float:
        return self.clock.now().timestamp()

    def on_frame(self, ts_ms: int, jpeg: bytes, motion: float) -> None:
        if self.paused:
            return
        now = self.now()
        self.frames.add(ts_ms, jpeg, motion, now)
        if motion >= self.s.motion_threshold:
            if self.motion_started is None:
                self.motion_started = now
            self.last_motion = now

    def on_audio(self, pcm: bytes) -> None:
        if self.paused:
            return
        rms = self.audio.add(pcm)
        burst = self.gate.feed(rms, self.audio.end, len(pcm) // 2)
        if burst and self._idle("hear"):
            self._start("hear", self._hear(*burst))

    async def on_client(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == "answer":
            await self._answer(str(message.get("choice", "")), by=str(message.get("by", "button")))
        elif kind == "typed":
            text = str(message.get("text", "")).strip()
            if text and not await self._answer_text(text, by="typing"):
                self._request(DecideRequest("spoken", text[:40], set(self.active_cues), self.now(), utterance=text))
        elif kind == "profile.set" and message.get("profile") in self.profiles:
            self.profile = self.profiles[message["profile"]]
            self.safety.profile = self.profile
            self.emit({"type": "profile", "profile": self.profile.id, "needs": list(self.profile.needs)})
        elif kind == "room.reset":
            self.reset()
        elif kind == "caregiver.ack":
            self._safety_effects(self.safety.acknowledge())
            self.emit({"type": "safety", "state": "acked"})
        elif kind == "live.pause":
            self.paused = bool(message.get("paused", True))
            if self.paused:
                self.frames.clear()
                self.audio.clear()

    def reset(self) -> None:
        self.emit(self.home.reset())
        self.consent.open = None
        self.safety = SafetyMachine(self.clock, self.profile)
        self.open_turn = None
        self.safety_turn = None
        self.latched.clear()
        self.active_cues.clear()
        self.pending = None
        self.emit({"type": "safety", "state": "ok"})

    def hello(self) -> list[dict[str, Any]]:
        """Events a newly connected screen needs to catch up."""
        return [
            {"type": "profile", "profile": self.profile.id, "needs": list(self.profile.needs)},
            {"type": "state", **self.home.state},
            {"type": "safety", "state": SAFETY_UI.get(self.safety.phase, "ok")},
        ]

    # ---------- scheduler ----------

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # keep the loop alive; one bad tick should not stop the feed
                log.exception("live tick failed")
            await asyncio.sleep(0.1)

    async def tick(self) -> None:
        self._safety_effects(self.safety.tick())
        update = self.consent.tick()
        if update is not None:
            self._consent_result(update, by="none")
        if self.paused:
            return
        now = self.now()
        if self._idle("watch") and len(self.frames.frames) >= 2:
            settled = self.last_motion is not None and now - self.last_motion >= self.s.settle_s
            long_motion = self.motion_started is not None and now - self.motion_started >= self.s.max_motion_wait_s
            heartbeat = now - self.last_watch >= self.s.heartbeat_s
            if (settled or long_motion or heartbeat) and self._budget():
                self.motion_started = None
                self.last_motion = None
                self.last_watch = now
                self._start("watch", self._watch())
        if self.pending is not None and self._idle("decide"):
            request, self.pending = self.pending, None
            if now - request.at <= self.s.stale_s:
                self._start("decide", self._decide(request))

    def _idle(self, lane: str) -> bool:
        task = self.tasks[lane]
        return task is None or task.done()

    def _start(self, lane: str, coro) -> None:
        self.tasks[lane] = asyncio.create_task(coro)

    def _budget(self) -> bool:
        now = self.now()
        while self.calls and now - self.calls[0] > 60:
            self.calls.popleft()
        if len(self.calls) >= self.s.budget_per_min:
            return False
        self.calls.append(now)
        return True

    def _request(self, request: DecideRequest) -> None:
        self.pending = request  # newer replaces older

    # ---------- watch lane ----------

    def _window(self, span: float, count: int, wav: bytes | None = None) -> Window | None:
        frames = self.frames.window(span, count)
        if len(frames) < 2:
            return None
        if wav is None:
            pcm = self.audio.last(span)
            wav = to_wav(pcm) if len(pcm) >= self.audio.rate else None  # at least 0.5 s
        return Window(frames=frames, wav=wav, seconds=self.frames.span_of(frames), ended_at=self.now())

    async def _watch(self) -> None:
        window = self._window(self.s.watch_span_s, self.s.watch_frames)
        if window is None:
            return
        read = await self.perceiver.read_scene(window, effort=self.s.watch_effort, profile=self.profile)
        if read.error or read.value is None:
            log.warning("scene read failed: %s", read.error)
            return
        scene: SceneState = read.value
        if window.wav is None or self.audio.loudest(self.s.watch_span_s) < self.gate.threshold:
            # The window was silent, so any sound label is a guess from the picture
            # (a bouncing ball read as a thud). Sounds come from the hear lane instead.
            scene = scene.model_copy(update={"sounds": ["none"]})
        self.scene = scene
        cues = derive(scene)
        self.emit({"type": "sense.see", "thumb": self._thumb(), "chips": _chips(scene, cues)})
        cleared = self.active_cues - cues
        for cue in cleared:
            self.latched.pop(cue, None)  # it cleared, so it may fire again later
        self.active_cues = cues
        self._on_cues(cues, trigger=" -> ".join(scene.timeline[-2:]) or scene.summary)

    def _on_cues(self, cues: set[str], *, trigger: str, wav: bytes | None = None) -> None:
        now = self.now()
        fresh = {c for c in cues if now - self.latched.get(c, -1e9) > self.s.latch_s}
        if not fresh:
            return
        for cue in fresh:
            self.latched[cue] = now
        if "fall" in fresh:
            self._safety_effects(self.safety.on_event("possible_fall"), trigger="fall seen")
        if "fall" in fresh:
            kind = "safety"
        elif fresh & NOTIFY_SOUNDS:
            kind = "sound"
            trigger = "heard: " + ", ".join(sorted(fresh & NOTIFY_SOUNDS))
        else:
            kind = "offer"
        self._request(DecideRequest(kind, trigger[:40] or ", ".join(sorted(fresh)), set(cues), now, wav=wav))

    # ---------- hear lane ----------

    async def _hear(self, start: int, end: int) -> None:
        wav = to_wav(self.audio.between(start, end))
        question = self._open_question()
        if question:
            read = await self.perceiver.answer(wav, question)
            choice = read.value if isinstance(read.value, str) else "unclear"
            if choice in {"yes", "no", "help"}:
                self.emit({"type": "sense.hear", "text": f"answer: {choice}"})
                await self._answer(choice, by="voice")
                return
        read = await self.perceiver.read_sound(wav)
        if read.error or read.value is None:
            log.warning("sound read failed: %s", read.error)
            return
        sound: SoundRead = read.value
        labels = {label for label in sound.sounds if label != "none"}
        notable = next((l for l in ("help", "alarm", "doorbell", "knock", "impact", "pain") if l in labels), None)
        self.emit({"type": "sense.hear", "text": sound.heard, "sound": notable})
        if "help" in labels:
            self._safety_effects(self.safety.on_event("distress_call"), trigger="call for help")
        cues = derive(apply_sound_read(self.scene, sound))
        before = dict(self.latched)
        self._on_cues(cues, trigger=sound.heard or ", ".join(sorted(labels)), wav=wav)
        fired = self.latched != before
        if not fired and "speech" in labels:
            self._request(
                DecideRequest(
                    "spoken",
                    (sound.heard or "speech")[:40],
                    set(self.active_cues),
                    self.now(),
                    wav=wav,
                    utterance=f"(speech in the attached audio) {sound.heard}",
                )
            )

    def _open_question(self) -> str | None:
        if self.safety.phase in {"checkin", "checkin_repeat"}:
            return CHECKIN_TEXT
        if self.consent.open is not None:
            return self.consent.open.question
        return None

    # ---------- decide lane ----------

    async def _decide(self, request: DecideRequest) -> None:
        turn_id = self.ids("turn")
        started = self.now()
        self.emit({"type": "turn.start", "turn_id": turn_id, "kind": request.kind, "trigger": request.trigger})
        for cue in sorted(request.cues):
            self.emit({"type": "think", "turn_id": turn_id, "note": f"cue: {cue.replace('_', ' ')}"})
        window = self._window(self.s.decide_span_s, self.s.decide_frames, wav=request.wav)
        if window is None:
            self._end(turn_id, started, "nothing", note="no frames yet")
            return
        self.emit({"type": "think", "turn_id": turn_id, "note": f"watching {window.seconds:.0f}s · {len(window.frames)} frames"})
        self._budget()
        result = await self.perceiver.decide(
            window,
            profile=self.profile,
            utterance=request.utterance,
            scene=self.scene,
            cues=request.cues,
            effort=self.s.decide_effort,
        )
        if self.now() - request.at > self.s.stale_s:
            self._end(turn_id, started, "nothing", note="too late, dropped")
            return
        if result.error:
            self._end(turn_id, started, "nothing", note="perceptron error")
            log.warning("decide failed: %s", result.error)
            return
        spoken = request.kind == "spoken"
        plan = _plan_from_devices(
            result.device,
            profile=self.profile,
            trigger=Trigger(kind="spoken" if spoken else "inferred", utterance=request.utterance or None, room="living_room"),
            snapshot_id=f"live_{turn_id}",
            reply=result.reply,
            clock=self.clock,
            ids=self.ids,
        )
        reasons = {call.name: call.reason for call in result.device}
        # Falls belong to the safety check-in ("Are you okay?", then the caregiver),
        # not to a second consent question. A model call to alert_caregiver starts
        # the check-in if it is not already running.
        wants_help = plan is not None and any(a.tool == "alert_caregiver" for a in plan.actions)
        fall_now = "fall" in request.cues or "fall" in derive(self.scene)
        if wants_help or fall_now:
            if wants_help:
                why = reasons.get("alert_caregiver") or "looks like a fall"
                self.emit({"type": "think", "turn_id": turn_id, "note": f"model: alert caregiver ({why})"[:48]})
            if self.safety.phase in {"idle", "watching"} and wants_help:
                self._safety_effects(self.safety.on_event("possible_fall"), trigger="model saw a fall")
                self._end(turn_id, started, "asked", note="check-in started")
            else:
                self._end(turn_id, started, "nothing", note="safety check-in handles this")
            return
        if plan is None:
            if result.reply:
                self.emit({"type": "say", "turn_id": turn_id, "text": result.reply})
            self._end(turn_id, started, "nothing", note="no tools, nothing needed")
            return
        update = self.consent.consider(plan, self.profile, transcript_clear=spoken, scene=self.scene)
        final = update.plan or plan
        turn = OpenTurn(turn_id)
        for action in final.actions:
            if action.tool == "alert_caregiver" and not update.release:
                self.emit({"type": "think", "turn_id": turn_id, "note": "alert caregiver? asking first"})
                continue
            self._tool_call(turn, action, reasons.get(action.tool, ""))
        outcome = self._consent_result(update, by=None, turn=turn)
        self._end(turn_id, started, outcome)

    def _tool_call(self, turn: OpenTurn, action, why: str) -> str:
        call_id = self.ids("call")
        turn.call_ids[action.action_id] = call_id
        self.emit(
            {
                "type": "tool.call",
                "turn_id": turn.turn_id,
                "call_id": call_id,
                "tool": action.tool,
                "args": action.args,
                "why": (why or action.tool.replace("_", " "))[:48],
                "kind": "device",
            }
        )
        return call_id

    def _end(self, turn_id: str, started: float, outcome: str, note: str | None = None) -> None:
        if note:
            self.emit({"type": "think", "turn_id": turn_id, "note": note})
        self.emit({"type": "turn.end", "turn_id": turn_id, "ms": int((self.now() - started) * 1000), "outcome": outcome})

    # ---------- consent ----------

    def _consent_result(self, update: ConsentUpdate, *, by: str | None, turn: OpenTurn | None = None) -> str:
        turn = turn or self.open_turn
        if turn is None:
            return "nothing"
        plan = update.plan
        if update.status == "approved" and plan is not None:
            if by:
                self.emit({"type": "ask.answer", "turn_id": turn.turn_id, "by": by, "choice": "yes"})
            self._run_plan(plan, turn)
            if self.open_turn is turn:
                self.open_turn = None
            return "acted"
        if update.status in {"asking", "repeated"}:
            question = update.question or "Should I do that?"
            if update.status == "asking":
                self.open_turn = turn
                for call_id in turn.call_ids.values():
                    self.emit({"type": "tool.status", "call_id": call_id, "plan_id": plan.plan_id if plan else None, "status": "asking"})
                self.emit({"type": "ask", "turn_id": turn.turn_id, "question": question, "choices": ["Yes", "No"]})
            self.emit({"type": "say", "turn_id": turn.turn_id, "text": question})
            return "asked"
        status = "declined" if update.status == "declined" else "rejected"
        if update.status == "declined":
            self.emit({"type": "ask.answer", "turn_id": turn.turn_id, "by": by or "none", "choice": "no"})
        for call_id in turn.call_ids.values():
            self.emit({"type": "tool.status", "call_id": call_id, "status": status, "detail": update.reason[:60]})
        if self.open_turn is turn:
            self.open_turn = None
        return "nothing"

    async def _answer(self, choice: str, *, by: str) -> None:
        choice = choice.lower()
        if self.safety.phase in {"checkin", "checkin_repeat"}:
            raw = "okay" if choice in {"yes", "okay", "ok"} else "help"
            if self.safety_turn:
                self.emit({"type": "ask.answer", "turn_id": self.safety_turn, "by": by, "choice": raw})
            self._safety_effects(self.safety.on_answer(raw))
            return
        if self.consent.open is None:
            return
        yes = choice in {"yes", "okay", "ok"}
        if by == "voice":
            update = self.consent.answer_voice("yes" if yes else "no")
        elif by == "typing":
            update = self.consent.answer_typing("yes" if yes else "no")
        else:
            update = self.consent.answer_button(yes)
        if update is not None:
            self._consent_result(update, by=by)

    async def _answer_text(self, text: str, *, by: str) -> bool:
        """Typed text answers an open question if it reads as yes, no, or help."""
        if self._open_question() is None:
            return False
        words = text.lower().strip(" .!?")
        for choice in ("help", "yes", "no", "okay", "ok"):
            if words == choice or words.startswith(choice + " "):
                await self._answer(choice, by=by)
                return True
        return False

    # ---------- doing things ----------

    def _run_plan(self, plan: Plan, turn: OpenTurn) -> None:
        for action in plan.actions:
            call_id = turn.call_ids.get(action.action_id) or self._tool_call(turn, action, "")
            self.emit({"type": "tool.status", "call_id": call_id, "plan_id": plan.plan_id, "status": "approved"})
            self._apply(action.tool, action.args, turn.turn_id)
            self.emit({"type": "tool.status", "call_id": call_id, "plan_id": plan.plan_id, "status": "done"})

    def _apply(self, tool: str, args: dict[str, Any], turn_id: str | None) -> None:
        state = self.home.apply(tool, args)
        if state is not None:
            self.emit(state)
        if tool == "flash_lights":
            self._later(float(args.get("duration_s", 3)), lambda: self.emit(self.home.end_flash()))
        elif tool == "show_caption":
            text = args.get("text")
            self._later(self.s.caption_s, lambda: self.home.state["caption"] == text and self.emit(self.home.clear_caption()))
        elif tool == "announce":
            self.emit({"type": "say", "turn_id": turn_id, "text": str(args.get("text", ""))})

    def _later(self, seconds: float, fn: Callable[[], Any]) -> None:
        async def run() -> None:
            await asyncio.sleep(seconds)
            fn()

        try:
            task = asyncio.get_running_loop().create_task(run())
        except RuntimeError:
            return
        self.timers.add(task)
        task.add_done_callback(self.timers.discard)

    def _safety_effects(self, effects: list[SafetyEffect], *, trigger: str = "safety") -> None:
        for effect in effects:
            if self.safety_turn is None:
                self.safety_turn = self.ids("turn")
                self.emit({"type": "turn.start", "turn_id": self.safety_turn, "kind": "safety", "trigger": trigger})
            turn = OpenTurn(self.safety_turn)
            if effect.kind == "checkin":
                self.emit({"type": "safety", "state": SAFETY_UI.get(self.safety.phase, "checkin1")})
                self.emit({"type": "ask", "turn_id": turn.turn_id, "question": effect.text, "choices": ["I'm okay", "Need help"]})
            for plan in effect.plans:
                for action in plan.actions:
                    why = {"alert_caregiver": "no answer or asked for help"}.get(action.tool, "safety check-in")
                    self._tool_call(turn, action, why)
                self._run_plan(plan, turn)
            if effect.kind == "alert":
                self.emit({"type": "safety", "state": "alerted"})
            elif effect.kind in {"notify_person"} and not effect.plans:
                self.emit({"type": "say", "turn_id": turn.turn_id, "text": effect.text})
            elif effect.kind == "cleared":
                self.emit({"type": "say", "turn_id": turn.turn_id, "text": effect.text})
                self.emit({"type": "turn.end", "turn_id": turn.turn_id, "ms": 0, "outcome": "nothing"})
                self.emit({"type": "safety", "state": "ok"})
                self.safety_turn = None

    def _thumb(self) -> str | None:
        frame = self.frames.latest
        if frame is None:
            return None
        return "data:image/jpeg;base64," + base64.b64encode(frame.jpeg).decode()


def _chips(scene: SceneState, cues: set[str]) -> list[str]:
    chips: list[str] = []
    for person in scene.people[:2]:
        if person.fell:
            chips.append("fell")
        if person.on_floor:
            chips.append("on floor")
        if person.posture != "unknown":
            chips.append(person.posture)
        if person.activity not in {"unknown", "idle"}:
            chips.append(person.activity)
        if person.eyes == "closed":
            chips.append("eyes closed")
        if person.gesture != "none":
            chips.append(person.gesture.replace("_", " "))
        chips.extend(sign.replace("_", " ") for sign in person.distress_signs[:2])
    if not scene.people:
        chips.append("nobody in view")
    if scene.lighting != "normal":
        chips.append(scene.lighting)
    chips.extend(label for label in scene.sounds if label not in {"none", "speech"})
    chips.extend(f"cue: {cue.replace('_', ' ')}" for cue in sorted(cues))
    seen: list[str] = []
    for chip in chips:
        if chip not in seen:
            seen.append(chip)
    return seen[:8]
