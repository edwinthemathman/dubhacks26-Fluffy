"""The live scheduler: camera and mic in, Perceptron reads out, tool calls to the room.

Lanes (at most one call in flight each):
  watch   motion then settle, a long stretch of motion, or a heartbeat -> scene read
  hear    the end of a burst of sound -> sound read (or a yes/no read while a question is open);
          a burst that lands while a read is in flight waits, and a newer one replaces it
  decide  a new cue or speech -> decision call with tools; a newer trigger replaces a queued
          one and results for a trigger older than `stale_s` are dropped
  safety  a fall or a call for help starts the check-in at once, without waiting on the model

Everything that happens is emitted as the web app's `FluffyEvent`s (web/src/stream/events.ts).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

from fluffy.brain.consent import ConsentManager, ConsentUpdate
from fluffy.brain.cues import NOTIFY_SOUNDS, derive, volume_request, wants_music_stop
from fluffy.brain.decide import _plan_from_devices, complete_paired_request
from fluffy.brain.instincts import Guess, Instincts
from fluffy.brain.objects import ObjectNotice, important_for, object_notices, tag_lines, wants_room_scan
from fluffy.brain.habits import HabitBook, habit_note, habit_why
from fluffy.brain.help import HelpLadder, HelpSignal
from fluffy.brain.safety import CHECKIN_TEXT, PAIN_TEXT_S, SafetyEffect, SafetyMachine
from fluffy.brain.wellbeing import WellbeingInput, assess, is_request, load_thresholds
from fluffy.clock import Clock
from fluffy.ids import IdFactory
from fluffy.integrations.spotify import pause_playback, play_clap_song, set_volume
from fluffy.live.buffers import AudioBuffer, FrameBuffer, SpeechGate, pad_pcm, to_wav
from fluffy.live.home import HomeState
from fluffy.live.perceive import Decision, Read, Window
from fluffy.live.presence import RoomPresence
from fluffy.live.verify import confirm, questions_for
from fluffy.live.voice_policy import VoicePolicy

_FIND = re.compile(
    r"(?:where (?:is|are) my|find my|i can(?:no|')?t find(?: my)?)\s+([a-z][a-z0-9 '-]{0,40})",
    re.I,
)


def _find_target(text: str) -> str | None:
    match = _FIND.search(text.lower())
    if match is None:
        return None
    name = match.group(1).strip(" ?.!,'\"")
    for tail in (" please", " right now", " for me"):
        if name.endswith(tail):
            name = name[: -len(tail)].strip()
    return name or None
from fluffy.integrations import run_tool
from fluffy.models import Action, Plan, Trigger
from fluffy.notify.photon import notify_fall, notify_person
from fluffy.perception.scene import SceneState, SoundRead, TagRead, TranscriptRead, apply_sound_read
from fluffy.profile.model import Profile
from fluffy.world.model import WorldModel, objects_at_end

log = logging.getLogger("fluffy.live")


def _agent_log(hypothesis: str, location: str, message: str, data: dict[str, Any]) -> None:
    # #region agent log
    try:
        import json
        import time

        with open("/Users/edwinan/Desktop/dubhacks26/.cursor/debug-9d207c.log", "a") as handle:
            handle.write(
                json.dumps(
                    {
                        "sessionId": "9d207c",
                        "hypothesisId": hypothesis,
                        "location": location,
                        "message": message,
                        "data": data,
                        "timestamp": int(time.time() * 1000),
                    }
                )
                + "\n"
            )
    except OSError:
        pass
    # #endregion

Emit = Callable[[dict[str, Any]], None]

SAFETY_UI = {"checkin": "checkin1", "checkin_repeat": "checkin2", "alerted": "alerted"}
FALL_CLEAR_S = 15


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
    scan_span_s: float = 8.0
    scan_frames: int = 12
    stale_s: float = 10.0
    latch_s: float = 30.0
    # Below this the mic is silence, so a sound label would be a guess from the picture.
    # Quiet speech sits under the burst gate and still has to ride with the frames.
    audible_floor: float = 80.0
    # A single squint or glasses reflection used to become glare immediately.
    glare_hold_s: float = 8.0
    glare_cooldown_s: float = 600
    budget_per_min: int = 40
    caption_s: float = 15.0


class Perceiver(Protocol):
    async def read_scene(self, window: Window, *, effort: str, profile: Profile | None = None) -> Read: ...
    async def read_sound(self, wav: bytes) -> Read: ...
    async def answer(self, wav: bytes, question: str) -> Read: ...
    async def transcript(self, wav: bytes, profile: Profile) -> Read: ...
    async def decide(
        self,
        window: Window,
        *,
        profile: Profile,
        utterance: str,
        scene: SceneState | None,
        cues: set[str],
        effort: str,
        asked: str = "",
        answer: str = "",
        room_change: str = "",
        habit_lines: list[str] | None = None,
        tag_lines: list[str] | None = None,
        important: str = "",
        memory: Any = None,
        now: Any = None,
    ) -> Decision: ...
    async def read_tags(self, window: Window, *, profile: Profile | None = None) -> Read: ...


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

    async def transcript(self, wav, profile):
        return await self._p.read_transcript(self.client, wav, profile)

    async def read_tags(self, window, *, profile=None):
        return await self._p.read_tags(self.client, window, profile=profile)

    async def locate(self, window, *, name, memory=None, now=None):
        return await self._p.locate(self.client, window, name, memory=memory, now=now)

    async def check(self, window, questions) -> dict[str, str]:
        """Ask each double-check question on the same frames, in parallel."""

        async def one(question) -> tuple[str, str]:
            read = await self._p.ask_frames(self.client, window, question.text, question.regex)
            value = read.value if isinstance(read.value, str) and read.value else "unclear"
            return question.key, value

        pairs = await asyncio.gather(*(one(question) for question in questions))
        return dict(pairs)

    async def decide(
        self,
        window,
        *,
        profile,
        utterance,
        scene,
        cues,
        effort,
        asked="",
        answer="",
        room_change="",
        habit_lines=None,
        tag_lines=None,
        important="",
        memory=None,
        now=None,
    ):
        return await self._p.decide(
            self.client,
            window,
            profile=profile,
            utterance=utterance,
            scene=scene,
            cues=cues,
            effort=effort,
            asked=asked,
            answer=answer,
            room_change=room_change,
            habit_lines=habit_lines,
            tag_lines=tag_lines,
            important=important,
            memory=memory,
            now=now,
        )


@dataclass
class DecideRequest:
    kind: str  # spoken | sound | offer | safety (turn kinds in the UI)
    trigger: str
    cues: set[str]
    at: float
    wav: bytes | None = None
    utterance: str = ""
    asked: str = ""
    answer: str = ""
    room_change: str = ""
    request_id: str = ""


@dataclass
class OpenTurn:
    """A plan waiting on the person's answer, and where its rows are on screen."""

    turn_id: str
    call_ids: dict[str, str] = field(default_factory=dict)  # action_id -> call_id


@dataclass
class StageTimings:
    """Milliseconds for one pass through the live lanes. None means that stage has not run."""

    frame_age_ms: int | None = None
    scene_read_ms: int | None = None
    decide_wait_ms: int | None = None
    decide_ms: int | None = None
    speech_to_tool_ms: int | None = None

    def as_dict(self) -> dict[str, int | None]:
        return {
            "frame_age_ms": self.frame_age_ms,
            "scene_read_ms": self.scene_read_ms,
            "decide_wait_ms": self.decide_wait_ms,
            "decide_ms": self.decide_ms,
            "speech_to_tool_ms": self.speech_to_tool_ms,
        }


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
        habits: HabitBook | None = None,
        on_clap: Callable[[], dict[str, Any]] | None = None,
        on_volume: Callable[[int], dict[str, Any]] | None = None,
        on_pause: Callable[[], dict[str, Any]] | None = None,
        delegate_execution: bool = False,
        submit_plan: Callable[[Plan, str], Awaitable[None]] | None = None,
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
        self.policy = VoicePolicy(self.clock)
        self.scene: SceneState | None = None
        self.scene_log: list[tuple] = []
        self.active_cues: set[str] = set()
        self.latched: dict[str, float] = {}
        self.calls: deque[float] = deque()
        self.motion_started: float | None = None
        self.last_motion: float | None = None
        self.last_watch = 0.0
        self.pending: DecideRequest | None = None
        self.pending_hear: tuple[int, int] | None = None
        self.tasks: dict[str, asyncio.Task | None] = {
            "watch": None,
            "hear": None,
            "decide": None,
            "scan": None,
        }
        self.world = WorldModel(self.clock, self.ids)
        self.notice_fired: dict[str, Any] = {}
        self.instincts = Instincts()
        self.open_instinct: str | None = None
        self.last_spoken = ""
        self.last_spoke_at = None
        self._floor_before: dict[str, bool] = {}
        self.texted_left: set[str] = set()
        self.want_scan = False
        self.scanned_empty = False
        self.open_turn: OpenTurn | None = None
        self.safety_turn: str | None = None
        self.paused = False
        self.epoch = 0
        self.mute_until = 0.0
        self.glare_since: float | None = None
        self.glare_quiet_until: float | None = None
        self.last_said = ""
        self.speech_at = 0.0
        self.habits = habits or HabitBook()
        self.on_clap = on_clap or play_clap_song
        self.on_volume = on_volume or set_volume
        self.on_pause = on_pause or pause_playback
        self.delegate_execution = delegate_execution
        self.submit_plan = submit_plan
        self.help = HelpLadder()
        self._later_request: DecideRequest | None = None
        self._gesture_last: list[str] = []
        self._gesture_until: dict[str, float] = {}
        self._upright_reads = 0
        self._player_checked = 0.0
        self._player_playing = False
        self._scan_at = 0.0
        self.still_frame: bytes | None = None
        self.capture_epoch = 0
        self.clap_played = False
        self.music_stopped = False
        self.presence = RoomPresence()
        self.room_change = ""
        self.says: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.timers: set[asyncio.Task] = set()
        self._help_text_sent = False
        self.timings = StageTimings()
        self._call_tools: dict[str, str] = {}
        self._checkin_text_sent = False
        self._pain_text_at: float | None = None
        self._fall_absent_since: float | None = None
        self._fall_was_here = False

    # ---------- inputs ----------

    def now(self) -> float:
        return self.clock.now().timestamp()

    def on_frame(self, ts_ms: int, jpeg: bytes, motion: float, *, still: bool = False) -> None:
        if self.paused:
            return
        if still:
            self.still_frame = jpeg
        now = self.now()
        self.frames.add(ts_ms, jpeg, motion, now)
        if motion >= self.s.motion_threshold:
            if self.motion_started is None:
                self.motion_started = now
            self.last_motion = now

    def on_audio(self, pcm: bytes) -> None:
        if self.paused or self.now() < self.mute_until:
            return
        rms = self.audio.add(pcm)
        burst = self.gate.feed(rms, self.audio.end, len(pcm) // 2)
        if not burst:
            return
        if self._idle("hear"):
            self._start("hear", self._hear(*burst))
        else:
            self.pending_hear = burst

    async def on_client(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == "answer":
            await self._answer(str(message.get("choice", "")), by=str(message.get("by", "button")))
        elif kind == "typed":
            text = str(message.get("text", "")).strip()
            if "scan the room" in text.lower():
                self.want_scan = True
            target = _find_target(text)
            if target and not await self._answer_text(text, by="typing"):
                await self._locate_and_say(target, None)
            elif text and not await self._answer_text(text, by="typing") and not self._spoken_volume(text):
                self._request(
                    DecideRequest(
                        "spoken",
                        text[:40],
                        set(self.active_cues),
                        self.now(),
                        utterance=text,
                        request_id=str(message.get("request_id") or ""),
                    )
                )
        elif kind == "room.scan":
            self.want_scan = True
            # #region agent log
            _agent_log("E", "loop.py:room.scan", "scan requested", {"frames": len(self.frames.frames), "paused": self.paused})
            # #endregion
        elif kind == "moment.save":
            self._save_moment()
        elif kind == "speech":
            await self._heard_speech(str(message.get("text", "")), confidence=message.get("confidence"))
        elif kind == "profile.set" and message.get("profile") in self.profiles:
            self.profile = self.profiles[message["profile"]]
            self.safety.profile = self.profile
            self.emit({"type": "profile", "profile": self.profile.id, "needs": list(self.profile.needs)})
        elif kind == "volume.set":
            self._set_speaker_volume(message.get("volume"))
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
            else:
                self.want_scan = True
        elif kind == "tts.start":
            self.mute_until = self.now() + 15
        elif kind == "tts.end":
            self.mute_until = self.now() + 0.3

    def reset(self) -> None:
        self.epoch += 1
        for task in list(self.timers):
            task.cancel()
        self.timers.clear()
        self.pending = None
        self.pending_hear = None
        self.emit(self.home.reset())
        self.consent.open = None
        self.safety = SafetyMachine(self.clock, self.profile)
        self.open_turn = None
        self.safety_turn = None
        self.latched.clear()
        self.music_stopped = False
        self.clap_played = False
        self.help = HelpLadder()
        self._later_request = None
        self._gesture_last.clear()
        self._upright_reads = 0
        self.active_cues.clear()
        self.glare_since = None
        self.glare_quiet_until = None
        self.last_said = ""
        self.speech_at = 0.0
        self.presence = RoomPresence()
        self.room_change = ""
        self.pending = None
        self.world = WorldModel(self.clock, self.ids)
        self.notice_fired.clear()
        self.instincts = Instincts()
        self.open_instinct = None
        self.last_spoken = ""
        self.last_spoke_at = None
        self.texted_left.clear()
        self._help_text_sent = False
        self._checkin_text_sent = False
        self._pain_text_at = None
        self._fall_absent_since = None
        self._fall_was_here = False
        self.want_scan = False
        self.scanned_empty = False
        self.timings = StageTimings()
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
        self._release_ended_fall()
        if (
            self._pain_text_at is not None
            and self.now() >= self._pain_text_at
            and self.safety.phase in {"checkin", "checkin_repeat"}
        ):
            self._pain_text_at = None
            self._text_checkin(CHECKIN_TEXT)
        was_unprompted = bool(self.consent.open and self.consent.open.unprompted)
        update = self.consent.tick()
        if update is not None:
            if update.reason == "no answer" and was_unprompted:
                self.policy.note_unanswered()
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
        if (
            self.want_scan
            and self._idle("scan")
            and len(self.frames.frames) >= 2
            and self._budget()
        ):
            self.want_scan = False
            self._scan_at = now
            # #region agent log
            _agent_log("E", "loop.py:tick", "scan starting", {"frames": len(self.frames.frames)})
            # #endregion
            self._start("scan", self._scan())
        elif (
            self._scan_at
            and self.scene is not None
            and self.scene.people
            and now - self._scan_at > 60
            and self._idle("scan")
        ):
            self.want_scan = True
        if self.pending is not None and self._idle("decide"):
            request = self.pending
            self.pending = self._later_request
            self._later_request = None
            if now - request.at <= self.s.stale_s:
                self._start("decide", self._decide(request))

    def _idle(self, lane: str) -> bool:
        task = self.tasks[lane]
        return task is None or task.done()

    def _start(self, lane: str, coro) -> None:
        async def guard() -> None:
            try:
                await coro
            except Exception:
                log.exception("%s lane failed", lane)

        self.tasks[lane] = asyncio.create_task(guard())

    def _live(self, epoch: int) -> bool:
        """False once the room was reset or the browser stopped streaming."""
        return epoch == self.epoch and not self.paused

    def on_feed_closed(self) -> None:
        """The last camera socket closed. Don't keep reading the last frame."""
        self.paused = True
        self.frames.clear()
        self.audio.clear()
        self.gate = SpeechGate()
        self.glare_since = None
        self.presence = RoomPresence()
        self.room_change = ""
        self.motion_started = None
        self.last_motion = None
        self.pending = None
        self.pending_hear = None

    def _budget(self) -> bool:
        now = self.now()
        while self.calls and now - self.calls[0] > 60:
            self.calls.popleft()
        if len(self.calls) >= self.s.budget_per_min:
            return False
        self.calls.append(now)
        return True

    def _request(self, request: DecideRequest, *, merge: bool = False) -> None:
        """Safety outranks a visual cue, and a visual cue outranks speech."""
        if merge and request.kind == "spoken":
            earlier = self.pending if self.pending is not None and self.pending.kind == "spoken" else self._later_request
            if earlier is not None and earlier.kind == "spoken" and 0 <= request.at - earlier.at <= 1.5:
                text = f"{earlier.utterance} {request.utterance}".strip()
                request = DecideRequest(
                    "spoken",
                    text[:40],
                    set(request.cues),
                    earlier.at,
                    wav=request.wav or earlier.wav,
                    utterance=text,
                    request_id=request.request_id or earlier.request_id,
                )
        rank = {"safety": 0, "spoken": 2}
        new_rank = rank.get(request.kind, 1)
        if self.pending is None:
            self.pending = request
            return
        old_rank = rank.get(self.pending.kind, 1)
        if new_rank < old_rank:
            self._later_request = self.pending
            self.pending = request
            return
        if new_rank > old_rank:
            self._later_request = request
            return
        self.pending = request

    # ---------- watch lane ----------

    def _window(self, span: float, count: int, wav: bytes | None = None) -> Window | None:
        frames = self.frames.window(span, count)
        if len(frames) < 2:
            return None
        # Silence stays frames-only, so the picture cannot invent a thud.
        # Anything louder, including quiet speech, goes out with the frames
        # the way a recording's soundtrack does.
        if self.delegate_execution:
            wav = None
        elif wav is None and self.audio.loudest(span) >= self.s.audible_floor:
            pcm = self.audio.last(span)
            wav = to_wav(pcm) if len(pcm) >= self.audio.rate else None  # at least 0.5 s
        return Window(frames=frames, wav=wav, seconds=self.frames.span_of(frames), ended_at=self.now())

    async def _watch(self) -> None:
        epoch = self.epoch
        window = self._window(self.s.watch_span_s, self.s.watch_frames)
        if window is None:
            return
        latest = self.frames.latest
        if latest is not None:
            self.timings.frame_age_ms = max(0, int((self.now() - latest.at) * 1000))
        if self.delegate_execution:
            self.emit({"type": "inference.status", "stage": "scene", "status": "started", "frames": len(window.frames)})
        started_read = time.perf_counter()
        effort = "low" if self.delegate_execution else self.s.watch_effort
        read = await self.perceiver.read_scene(window, effort=effort, profile=self.profile)
        self.timings.scene_read_ms = int((time.perf_counter() - started_read) * 1000)
        if not self._live(epoch):
            return
        if read.error or read.value is None:
            log.warning("scene read failed: %s", read.error)
            return
        scene: SceneState = read.value
        if self.delegate_execution:
            scene = scene.model_copy(update={"said": ""})
            self.emit(
                {
                    "type": "inference.status",
                    "stage": "scene",
                    "status": "completed",
                    "frames": len(window.frames),
                    "elapsed_ms": int((time.perf_counter() - started_read) * 1000),
                    "frame_age_ms": self.timings.frame_age_ms,
                    "thinking": read.thinking or "",
                }
            )
        scene = scene.model_copy(update={"objects_seen": objects_at_end(scene)})
        if window.wav is None or self.audio.loudest(self.s.watch_span_s) < self.s.audible_floor:
            # The window was silent, so any sound label or quote is a guess from the picture
            # (a bouncing ball read as a thud). Real sounds come from the mic.
            scene = scene.model_copy(update={"sounds": ["none"], "said": ""})
        scene = _clap_not_impact(scene)
        self.scene = scene
        self._notice_scene_gesture(scene)
        self._emit_perception("scene")
        self.scene_log.append((self.clock.now(), scene))
        self.scene_log = self.scene_log[-8:]
        self._consider_safety(scene)
        presence = self.presence.observe(scene, self.now(), self.profile.agreements.person_name)
        self.room_change = presence.room_change
        cues = self._scene_cues(scene, presence.cues)
        self._remember_objects(scene, cues)
        self.emit({"type": "sense.see", "thumb": self._thumb(), "chips": _chips(scene, cues, self._tag_chips()), "tags": self._tag_payload()})
        cleared = self.active_cues - cues
        for cue in cleared:
            self.latched.pop(cue, None)  # it cleared, so it may fire again later
        self.active_cues = cues
        self._on_cues(cues, trigger=" -> ".join(scene.timeline[-2:]) or scene.summary)
        self._consider_scan(scene, cues)
        if self.safety.phase in {"idle", "watching"} and "fall" not in cues:
            await self._handle_notices(scene, cues, window)
        said = scene.said.strip()
        if not said:
            if not self._recent_speech():
                self.last_said = ""
        elif said != self.last_said and not self._recent_speech():
            # Same path as a recording: the words on the scene read are the transcript.
            self.last_said = said
            self._request(
                DecideRequest(
                    "spoken",
                    said[:40],
                    set(self.active_cues),
                    self.now(),
                    wav=window.wav,
                    utterance=said,
                    room_change=self.room_change,
            )
        )

    def _remember_objects(self, scene: SceneState, cues: set[str]) -> None:
        self._floor_before = self.world.objects.floor_flags()
        self.world.objects.note_live(scene.objects_seen, self.clock.now())

    def _consider_scan(self, scene: SceneState, cues: set[str]) -> None:
        empty_for = 0.0
        if not scene.people and self.presence.empty_first_at is not None:
            empty_for = max(0.0, self.now() - self.presence.empty_first_at)
        if scene.people:
            self.scanned_empty = False
        if "entered_room" in cues:
            self.texted_left.clear()
        reason = wants_room_scan(
            empty_for_s=empty_for,
            entered="entered_room" in cues,
            said=scene.said or "",
            already_scanned_empty=self.scanned_empty,
        )
        if reason == "empty":
            self.scanned_empty = True
        if reason:
            self.want_scan = True

    async def _handle_notices(self, scene: SceneState, cues: set[str], window: Window | None = None) -> None:
        now = self.clock.now()
        self.instincts.note(scene, now)
        guess = self.instincts.guess(
            scene,
            self.world.objects,
            self.profile,
            now,
            music_on=self._music_on(),
            spoke_recently=self._spoke_recently(now),
            last_line=self.last_spoken,
        )
        guess = await self._confirm_guess(guess, window)
        observed = any(person.actions for person in scene.people if not person.on_screen)
        if guess is not None and guess.kind == "safety":
            self._perform_guess(guess, now)
            return
        if guess is not None and guess.kind in {"volume_up", "stop_music", "repeat"}:
            self._perform_guess(guess, now)
            return
        if guess is not None and self._instinct_allowed(guess):
            self._perform_guess(guess, now)
            observed = True
        departing = "left_room" in cues or any(person.activity == "leaving" for person in scene.people)
        notices = object_notices(
            scene,
            self.world.objects,
            self.profile,
            now,
            departing=departing,
            previous_floor=self._floor_before,
            fired=self.notice_fired,
            skip_search=observed,
        )
        for notice in notices:
            if notice.kind in {"searching", "out_of_reach"}:
                self._offer_notice(notice)
            else:
                self._tell_notice(notice)
            if notice.kind == "left_behind" and "left_room" in cues and notice.name not in self.texted_left:
                text = f"You left your {notice.name} {notice.location}."
                if notify_person(text):
                    self.texted_left.add(notice.name)

    def _tell_notice(self, notice) -> None:
        turn_id = self.ids("turn")
        started = self.now()
        self.emit(
            {
                "type": "turn.start",
                "turn_id": turn_id,
                "kind": "offer",
                "trigger": notice.kind.replace("_", " "),
            }
        )
        if getattr(notice, "chain", ""):
            self.emit({"type": "think", "turn_id": turn_id, "note": notice.chain[:240]})
        turn = OpenTurn(turn_id)
        channels = set(self.profile.delivery_channels())
        steps: list[tuple[str, dict[str, Any]]] = []
        if "speech" in channels:
            steps.append(("announce", {"text": notice.text, "room": "living_room"}))
        if "caption" in channels:
            steps.append(("show_caption", {"text": notice.text[:140], "size": "large", "priority": "normal"}))
        if "light" in channels:
            steps.append(
                ("flash_lights", {"room": "living_room", "color": "blue", "pattern": "pulse1", "duration_s": 3})
            )
        if not steps:
            steps.append(("show_caption", {"text": notice.text[:140], "size": "large", "priority": "normal"}))
        for tool, args in steps:
            action = Action(action_id=self.ids("act"), tool=tool, args=args)
            call_id = self._tool_call(turn, action, notice.kind.replace("_", " "))
            self._apply(tool, args, turn_id)
            self._tool_status(call_id, "done", tool=tool, result={"ok": True})
        self._end(turn_id, started, "acted")

    def _offer_notice(self, notice) -> None:
        turn_id = self.ids("turn")
        started = self.now()
        self.emit(
            {
                "type": "turn.start",
                "turn_id": turn_id,
                "kind": "offer",
                "trigger": notice.kind.replace("_", " "),
            }
        )
        if getattr(notice, "chain", ""):
            self.emit({"type": "think", "turn_id": turn_id, "note": notice.chain[:240]})
        request = DecideRequest("offer", notice.text[:40], set(self.active_cues), started)
        self._ask_user(turn_id, started, request, notice.text, list(notice.choices) or None)

    async def _confirm_guess(self, guess: Guess | None, window: Window | None) -> Guess | None:
        if guess is None or not guess.check:
            return guess
        questions = questions_for(guess.need)
        checker = getattr(self.perceiver, "check", None)
        if checker is None or window is None or not questions:
            return guess
        try:
            answers = await checker(window, questions)
        except Exception as exc:
            log.warning("instinct check failed: %s", exc)
            return None
        checked = confirm(guess, answers)
        if checked is None:
            turn_id = self.ids("turn")
            started = self.now()
            self.emit({"type": "turn.start", "turn_id": turn_id, "kind": "offer", "trigger": "instinct"})
            self.emit({"type": "think", "turn_id": turn_id, "note": "not sure, staying quiet"})
            self._end(turn_id, started, "nothing", note="double-check disagreed")
        return checked

    def _instinct_allowed(self, guess: Guess) -> bool:
        hold = self.policy.hold_reason(
            someone_present=self._someone_present(),
            question_open=self._open_question() is not None,
            question=guess.text,
        )
        if hold:
            return False
        if guess.kind == "tell":
            self.policy.note_unprompted()
        return True

    def _spoke_recently(self, now) -> bool:
        if self.last_spoke_at is None:
            return False
        return (now - self.last_spoke_at).total_seconds() < 10

    def _perform_guess(self, guess: Guess, now) -> None:
        self.instincts.note_done(guess.need, now)
        if guess.kind == "safety":
            outcome = self.help.observe(HelpSignal(guess.need, "strong", "vision", self.now()))
            self._start_help(outcome, trigger=guess.need.replace("_", " "))
            return
        if guess.kind == "volume_up":
            self.note_gesture("cup_ear", agreed=True)
            return
        if guess.kind == "stop_music":
            self.note_gesture("stop", agreed=True)
            return
        if guess.kind == "repeat":
            self._say(None, guess.text, voice=True)
            return
        notice = ObjectNotice(
            "where" if guess.kind == "tell" else "searching",
            guess.need,
            "",
            guess.text,
            guess.choices,
            chain=guess.chain,
        )
        if guess.kind == "ask":
            self.open_instinct = guess.need
            self._offer_notice(notice)
            return
        self._tell_notice(notice)

    def _answer_instinct(self, need: str, choice: str, question: str, turn, by: str) -> None:
        if choice in {"no", "nope", "nah", "i'm fine", "im fine", "i am fine"}:
            self.instincts.mute(need, self.clock.now())
            self.policy.note_declined_question(question)
            if turn is not None:
                self.emit({"type": "ask.answer", "turn_id": turn.turn_id, "by": by, "choice": "no"})
            return
        if turn is not None:
            self.emit({"type": "ask.answer", "turn_id": turn.turn_id, "by": by, "choice": choice[:40]})
        if choice in {"something else", "other"}:
            self.open_instinct = need
            self._offer_notice(ObjectNotice("searching", need, "", "What are you looking for?", ()))
            return
        name = choice.removeprefix("your ").strip()
        recall = self.world.objects.recall(name, self.clock.now())
        if recall.status == "unknown" or not (recall.location_desc or recall.home_desc):
            self._follow_up(choice, question, None)
            return
        place = recall.location_desc or recall.home_desc or "in the room"
        verb = "are" if name.endswith("s") else "is"
        self._say(turn.turn_id if turn is not None else None, f"Your {name} {verb} {place}.", voice=True)

    def _tag_chips(self) -> list[str]:
        chips = []
        for tag in self.world.objects.tags(self.clock.now(), important_for(self.profile))[:4]:
            if tag.on_floor:
                place = "floor"
            else:
                words = [word for word in tag.location_desc.split() if word not in {"the", "a", "on", "by", "near"}]
                place = words[-1] if words else "room"
            chips.append(f"{tag.name} · {place}")
        return chips

    def _tag_payload(self) -> list[dict[str, str]]:
        return [
            {"name": tag.name, "where": tag.location_desc}
            for tag in self.world.objects.tags(self.clock.now(), important_for(self.profile))
        ]

    async def _scan(self) -> None:
        epoch = self.epoch
        window = self._window(self.s.scan_span_s, self.s.scan_frames)
        if not hasattr(self.perceiver, "read_tags"):
            return
        if window is None:
            self.want_scan = True
            # #region agent log
            _agent_log("E", "loop.py:_scan", "scan waiting for frames", {"frames": len(self.frames.frames)})
            # #endregion
            return
        read = await self.perceiver.read_tags(window, profile=self.profile)
        if not self._live(epoch) or read.error or not isinstance(read.value, TagRead):
            # #region agent log
            _agent_log("E", "loop.py:_scan", "scan read failed", {"error": read.error, "paused": self.paused, "kind": type(read.value).__name__ if read.value is not None else None})
            # #endregion
            return
        self.world.objects.note_scan(read.value.objects, self.clock.now())
        scene = self.scene or SceneState()
        self.emit(
            {
                "type": "sense.see",
                "thumb": self._thumb(),
                "chips": _chips(scene, self.active_cues, self._tag_chips()),
                "tags": self._tag_payload(),
            }
        )

    def _scene_cues(self, scene: SceneState, presence: set[str]) -> set[str]:
        """Live arrivals come from the room tracker, not one scene read."""
        cues = self._held_cues(derive(scene))
        if "entered_room" not in presence:
            cues.discard("arriving")
        return cues | presence

    def _lights_are_up(self) -> bool:
        lamp = int(self.home.state["lamp"]["luminosity"])
        ceiling = int(self.home.state["ceiling"]["luminosity"])
        return lamp >= 40 or ceiling >= 40

    def _held_cues(self, cues: set[str]) -> set[str]:
        """Glare has to stay in view, and only while the room is actually bright."""
        now = self.now()
        if "glare" not in cues:
            self.glare_since = None
            return cues
        cooling = self.glare_quiet_until is not None and now < self.glare_quiet_until
        if cooling or not self._lights_are_up():
            self.glare_since = None
            return set(cues) - {"glare"}
        if self.glare_since is None:
            self.glare_since = now
        if now - self.glare_since < self.s.glare_hold_s:
            return set(cues) - {"glare"}
        return cues

    def _consider_safety(self, scene: SceneState) -> None:
        """Check-in only when the wellbeing score clears the bar. A request cancels one."""

        said = (scene.said or "").strip()
        result = assess(
            WellbeingInput(
                transcript=said,
                scenes=list(self.scene_log),
                now=self.clock.now(),
                last_speech_at=self.clock.now() if said else None,
                thresholds=load_thresholds(),
            )
        )
        if result.status == "request" and self.safety.phase in {"checkin", "checkin_repeat"}:
            self._safety_effects(self.safety.on_answer(said))
            return
        upright = any(person.posture in {"standing", "walking"} for person in scene.people)
        if upright and self.safety.phase in {"checkin", "checkin_repeat"}:
            self._safety_effects(self.safety.on_answer("I'm okay"))
            return
        if result.status == "possibly_down" and self.safety.phase in {"idle", "watching"}:
            self._safety_effects(self.safety.on_event("possible_fall"), trigger="fall seen")
        elif result.status == "debilitated" and self.safety.phase in {"idle", "watching"}:
            self._safety_effects(self.safety.on_event("distress_call", source="sos_button"), trigger="sos")

    def _on_cues(self, cues: set[str], *, trigger: str, wav: bytes | None = None) -> None:
        now = self.now()
        # The first clap starts the song. It is never started again.
        playing = self._music_on()
        if "clapping" in cues and not self.clap_played:
            self.clap_played = True
            self._play_claps()
        if (
            "stop_music" in cues
            and playing
            and not self.music_stopped
            and now - self.latched.get("stop_music", -1e9) > 30
        ):
            self.latched["stop_music"] = now
            self._stop_music()
        cues = set(cues) - {"clapping", "stop_music"}
        fresh = {c for c in cues if now - self.latched.get(c, -1e9) > self.s.latch_s}
        if not fresh:
            return
        for cue in fresh:
            self.latched[cue] = now
        if "glare" in fresh:
            self.glare_quiet_until = now + self.s.glare_cooldown_s
        if "fall" in fresh:
            fell = self.scene is not None and any(person.fell for person in self.scene.people)
            outcome = (
                self.help.observe(HelpSignal("fell", "strong", "vision", now))
                if fell
                else self.help.note_floor(now)
            )
            self._start_help(outcome, trigger="fall seen")
            kind = "safety"
        elif fresh & NOTIFY_SOUNDS:
            kind = "sound"
            trigger = "heard: " + ", ".join(sorted(fresh & NOTIFY_SOUNDS))
        else:
            kind = "offer"
        self._request(
            DecideRequest(
                kind,
                trigger[:40] or ", ".join(sorted(fresh)),
                set(cues),
                now,
                wav=wav,
                room_change=self.room_change,
            )
        )

    def _play_claps(self) -> None:
        """A seen or heard clap starts the same song, without asking."""
        started = self.now()
        turn_id = self.ids("turn")
        self.emit({"type": "turn.start", "turn_id": turn_id, "kind": "sound", "trigger": "clapping"})
        self.emit({"type": "think", "turn_id": turn_id, "note": "clapping, playing the song"})
        try:
            result = self.on_clap()
        except Exception as exc:
            log.warning("clap song failed: %s", exc)
            result = {"ok": False, "error": "could not reach Spotify"}
        track = str(result.get("track") or "Should I Stay or Should I Go - Remastered")
        artist = str(result.get("artist") or "The Clash")
        turn = OpenTurn(turn_id)
        action = Action(
            action_id=self.ids("act"),
            tool="spotify_play",
            args={"track": track, "artist": artist, "query": f"{track} {artist}"},
        )
        call_id = self._tool_call(turn, action, "clapping")
        if result.get("ok"):
            heard_volume = result.get("volume")
            volume = heard_volume if isinstance(heard_volume, int) else self.home.state["speaker"]["volume"]
            state = self.home.apply("play_sound", {"sound": track, "room": "living_room", "volume": volume})
            if state is not None:
                self.emit(state)
            where = result.get("device") or "Spotify"
            self._tool_status(call_id, "done", result=result)
            self._end(turn_id, started, "acted", note=f"playing {track} on {where}")
            return
        self._tool_status(call_id, "rejected", detail=result.get("error") or "could not play")
        self._end(turn_id, started, "nothing", note=str(result.get("error") or "could not play the song"))

    def _set_speaker_volume(self, raw: Any) -> None:
        try:
            level = int(raw)
        except (TypeError, ValueError):
            return
        level = max(0, min(100, level))
        self.home.state["speaker"]["volume"] = level
        self.emit({"type": "state", "speaker": dict(self.home.state["speaker"])})
        self._spotify_volume(level)

    def _spotify_volume(self, raw: Any) -> None:
        try:
            level = max(0, min(100, int(raw)))
        except (TypeError, ValueError):
            return
        try:
            result = self.on_volume(level)
        except Exception as exc:
            log.warning("spotify volume failed: %s", exc)
            return
        if not result.get("ok"):
            log.warning("spotify volume: %s", result.get("error"))

    def _spoken_volume(self, text: str) -> bool:
        """Louder, quieter, or a number sets Spotify's volume without asking."""
        request = volume_request(text)
        if request is None:
            return False
        current = int(self.home.state["speaker"].get("volume") or 0)
        op = request["op"]
        if op == "mute":
            level = 0
        elif op == "set":
            level = int(request["level"])
        else:
            if current <= 0:
                current = 40
            level = current + (20 if op == "up" else -20)
        self._set_speaker_volume(max(0, min(100, level)))
        return True

    def _start_help(self, outcome: str, *, trigger: str) -> None:
        if outcome == "confirm":
            outcome = self.help.confirm(
                on_floor="yes" if self._still_down() else "unclear",
                hurt="yes" if self._looks_distressed() else "unclear",
                okay="yes" if self._upright_and_moving() else "unclear",
            )
        if outcome != "checkin":
            return
        kind = "distress_call" if trigger in {"call for help", "pain", "asked for help"} else "possible_fall"
        self._safety_effects(self.safety.on_event(kind, strength=self.help.strength or "strong"), trigger=trigger)

    def _words_are_theirs(self, confidence: float) -> bool:
        """A quiet or off-screen phrase is not a command. No score means we cannot tell."""
        if confidence >= 0.8:
            return True
        if self.scene is None or not self.scene.people:
            return confidence >= 0.5
        if any(person.speaking != "no" for person in self.scene.people):
            return confidence >= 0.5
        return False

    def _person_speaking(self) -> bool:
        if self.scene is None or not self.scene.people:
            return False
        return any(person.speaking == "yes" for person in self.scene.people)

    def _still_down(self) -> bool:
        if self.scene is None:
            return False
        return any(person.on_floor or person.fell for person in self.scene.people)

    def _looks_distressed(self) -> bool:
        if self.scene is None:
            return False
        for person in self.scene.people:
            signs = set(person.distress_signs) - {"motionless"}
            if signs or person.activity == "distressed":
                return True
        return False

    def _upright_and_moving(self) -> bool:
        if self.scene is None or not self.scene.people:
            return False
        person = self.scene.people[0]
        if person.on_floor or person.fell or person.posture == "lying":
            return False
        return person.posture == "standing" or person.activity in {"walking", "arriving"}

    def _is_ah(self, heard: str) -> bool:
        text = " ".join(heard.lower().split())
        return text in {"ah", "ahh", "sigh", "a sigh", "gasp", "sharp gasp"}

    def _notice_scene_gesture(self, scene: SceneState) -> None:
        for person in scene.people:
            if person.on_phone == "yes":
                continue
            if person.gesture == "stop":
                self.note_gesture("stop", agreed=True)
            if "cup_ear" in person.actions:
                self.note_gesture("cup_ear", agreed=True)

    def note_gesture(self, label: str, *, agreed: bool = False) -> str | None:
        """A stop palm or a cupped ear. Two reads, or one read the scene agrees with."""
        if label not in {"stop", "cup_ear"}:
            self._gesture_last.clear()
            return None
        if label == "cup_ear" and self.scene is not None and any(person.on_phone == "yes" for person in self.scene.people):
            return None
        self._gesture_last.append(label)
        self._gesture_last = self._gesture_last[-2:]
        if not agreed and self._gesture_last != [label, label]:
            return None
        self._gesture_last.clear()
        if label == "stop":
            if not self._music_on():
                return None
            self._stop_music()
            return "stop"
        if self.now() < self._gesture_until.get("cup", 0):
            return None
        self._gesture_until["cup"] = self.now() + 10
        if self._music_on():
            current = int(self.home.state["speaker"].get("volume") or 0)
            self._set_speaker_volume(min(100, current + 20))
            self._say(None, "Turning the music up.", voice=True)
            return "louder"
        return None

    def _music_on(self) -> bool:
        if self.home.state["speaker"].get("sound"):
            self.music_stopped = False
            return True
        if self.now() - self._player_checked < 3:
            return self._player_playing
        self._player_checked = self.now()
        try:
            from fluffy.integrations.spotify_legacy import TOKEN_PATH, Spotify

            if not TOKEN_PATH.exists():
                self._player_playing = False
                return False
            status = Spotify().now_playing()
        except Exception:
            self._player_playing = False
            return False
        self._player_playing = bool(status.get("playing"))
        if self._player_playing:
            self.music_stopped = False
            volume = status.get("volume")
            if isinstance(volume, int):
                self.home.state["speaker"]["volume"] = volume
            if not self.home.state["speaker"].get("sound"):
                self.home.state["speaker"]["sound"] = "music"
        return self._player_playing

    def _stop_music(self) -> None:
        """A raised palm, or the words stop, pauses Spotify. It does not ask."""
        started = self.now()
        turn_id = self.ids("turn")
        self.emit({"type": "turn.start", "turn_id": turn_id, "kind": "sound", "trigger": "stop"})
        self.emit({"type": "think", "turn_id": turn_id, "note": "stopping the music"})
        result = self._pause_spotify()
        turn = OpenTurn(turn_id)
        action = Action(action_id=self.ids("act"), tool="spotify_pause", args={})
        call_id = self._tool_call(turn, action, "stop")
        if result.get("ok"):
            state = self.home.apply("stop_sound", {"room": "living_room"})
            if state is not None:
                self.emit(state)
            self._tool_status(call_id, "done", result=result)
            self._say(turn_id, "Okay, I stopped the music.", voice=True)
            self._end(turn_id, started, "acted", note="music stopped")
            return
        self._tool_status(call_id, "rejected", detail=result.get("error") or "could not pause")
        self._say(turn_id, "I couldn't reach Spotify.", voice=True)
        self._end(turn_id, started, "nothing", note=str(result.get("error") or "could not stop the music"))

    def _pause_spotify(self) -> dict[str, Any]:
        try:
            result = self.on_pause()
        except Exception as exc:
            log.warning("spotify pause failed: %s", exc)
            return {"ok": False, "error": "could not reach Spotify"}
        if result.get("ok"):
            self.music_stopped = True
        else:
            log.warning("spotify pause: %s", result.get("error"))
        return result

    # ---------- hear lane ----------

    async def _hear(self, start: int, end: int) -> None:
        epoch = self.epoch
        while self._live(epoch):
            await self._hear_span(start, end, epoch)
            nxt = self.pending_hear
            self.pending_hear = None
            if nxt is None or not self._live(epoch):
                return
            start, end = nxt

    async def _hear_span(self, start: int, end: int, epoch: int) -> None:
        # Pad under half a second. A shorter clip makes the decoder report zero frames.
        wav = to_wav(pad_pcm(self.audio.between(start, end)))
        if self.delegate_execution:
            await self._classify_sound(wav, epoch)
            return
        question = self._open_question()
        if question:
            read = await self.perceiver.answer(wav, question)
            if not self._live(epoch):
                return
            choice = read.value if isinstance(read.value, str) else "unclear"
            if choice in {"yes", "no", "help"}:
                self.emit({"type": "sense.hear", "text": f"answer: {choice}"})
                await self._answer(choice, by="voice")
                return
            if choice == "other":
                self.emit({"type": "sense.hear", "text": "answer: other"})
                spoken = await self.perceiver.transcript(wav, self.profile)
                if not self._live(epoch):
                    return
                text = spoken.value.transcript.strip() if isinstance(spoken.value, TranscriptRead) else ""
                if text:
                    question = self._replace_open_question(by="voice", said=text)
                    self._follow_up(text, question, wav)
                return
        read = await self.perceiver.read_sound(wav)
        if not self._live(epoch):
            return
        if read.error or read.value is None:
            log.warning("sound read failed: %s", read.error)
            return
        sound: SoundRead = read.value
        labels = {label for label in sound.sounds if label != "none"}
        if "clap" in labels or "clap" in (sound.heard or "").lower():
            labels.discard("impact")
            labels.add("clap")
            sound = sound.model_copy(update={"sounds": sorted(labels)})
        notable = next((l for l in ("help", "alarm", "doorbell", "knock", "clap", "pain", "impact") if l in labels), None)
        self.emit({"type": "sense.hear", "text": sound.heard, "sound": notable})
        if "help" in labels:
            speaking = self._person_speaking()
            tier = "strong" if speaking else "weak"
            self._start_help(self.help.observe(HelpSignal("help", tier, "words" if speaking else "sound", self.now())), trigger="call for help")
        elif "pain" in labels and not self._is_ah(sound.heard):
            outcome = self.help.observe(HelpSignal("pain", "supporting", "sound", self.now()))
            if self._looks_distressed():
                outcome = self.help.observe(HelpSignal("distress", "supporting", "vision", self.now()))
            self._start_help(outcome, trigger="pain")
        heard_scene = apply_sound_read(self.scene, sound)
        cues = self._scene_cues(heard_scene, set()) if heard_scene is not None else set()
        if "clap" in labels:
            cues.add("clapping")
        before = dict(self.latched)
        self._on_cues(cues, trigger=sound.heard or ", ".join(sorted(labels)), wav=wav)
        if "speech" not in labels or self._recent_speech():
            return
        # Chrome already supplied the words. This call is the fallback.
        spoken = await self.perceiver.transcript(wav, self.profile)
        if not self._live(epoch):
            return
        text = spoken.value.transcript.strip() if isinstance(spoken.value, TranscriptRead) else ""
        if not text and "speech" not in labels:
            return
        utterance = text or sound.heard or "speech"
        self.last_said = utterance
        if self._spoken_volume(utterance):
            return
        self._request(
            DecideRequest(
                "spoken",
                utterance[:40],
                set(self.active_cues),
                self.now(),
                wav=wav,
                utterance=utterance,
            )
        )

    async def _heard_speech(self, text: str, confidence: float | None = None) -> None:
        """Chrome's live transcript. The words are the decision, the way a typed line is."""
        text = text.strip()
        if not text:
            return
        if isinstance(confidence, (int, float)) and not self._words_are_theirs(float(confidence)):
            return
        self.speech_at = self.now()
        self.timings.speech_to_tool_ms = None
        self.last_said = text
        if "scan the room" in text.lower():
            self.want_scan = True
        self.emit({"type": "sense.hear", "text": text})
        if await self._answer_text(text, by="voice"):
            return
        target = _find_target(text)
        if target:
            await self._locate_and_say(target, None)
            return
        if wants_music_stop(text) and self._music_on() and not self.music_stopped:
            self._stop_music()
            return
        if self._spoken_volume(text):
            return
        self._request(
            DecideRequest("spoken", text[:40], set(self.active_cues), self.now(), utterance=text),
            merge=True,
        )

    def _recent_speech(self) -> bool:
        return self.now() - self.speech_at < 8

    async def _locate_and_say(self, name: str, turn_id: str | None) -> None:
        """A fresh tag is spoken at once. Otherwise one targeted look, preferring a still frame."""
        recall = self.world.objects.recall(name, self.clock.now())
        fresh = (
            recall.status == "fresh"
            and recall.last_seen_at is not None
            and (self.clock.now() - recall.last_seen_at).total_seconds() <= 120
            and bool(recall.location_desc)
        )
        if fresh:
            from fluffy.brain.instincts import place_phrase, speakable

            sentence = speakable(f"Your {name} are {place_phrase(recall.location_desc, recall.side, 'unclear')}.")
        else:
            finder = getattr(self.perceiver, "locate", None)
            window = None
            if finder is not None:
                self.still_frame = None
                self.emit({"type": "still"})
                deadline = time.perf_counter() + 0.4
                while self.still_frame is None and time.perf_counter() < deadline:
                    await asyncio.sleep(0.05)
                window = self._still_window() or self._window(self.s.decide_span_s, self.s.decide_frames)
            if finder is not None and window is not None:
                located = await finder(window, name=name, memory=self.world.objects, now=self.clock.now())
                sentence = located.sentence
            else:
                from fluffy.live.perceive import _miss_line

                sentence = _miss_line(name, self.world.objects, self.clock.now())
        self._say(turn_id, sentence, voice=True)
        self.emit({"type": "perception", "lane": "locate", "objects": [f"{name}: {sentence}"], "actions": [], "calls": [{"name": "find_object", "args": {"name": name}, "kept": True}]})

    def _still_window(self) -> Window | None:
        jpeg = self.still_frame
        if not jpeg:
            return None
        return Window(frames=[(jpeg, 0), (jpeg, 400)], seconds=0.4, ended_at=self.now())

    async def _run_meta(self, turn_id: str, calls: list) -> None:
        for call in calls:
            if call.name == "find_object":
                await self._locate_and_say(str(call.args.get("name") or "that"), turn_id)
            elif call.name == "describe_scene":
                summary = self.scene.summary.strip() if self.scene is not None else ""
                self._say(turn_id, summary or "I don't have a clear view of the room right now.", voice=True)
            elif call.name == "read_text":
                text = self.scene.text_present if self.scene is not None else None
                self._say(turn_id, text or "I don't see any writing from here.", voice=True)
            elif call.name == "look":
                question = str(call.args.get("question") or "What do you see?")
                self._say(turn_id, self.scene.summary.strip() if self.scene and self.scene.summary else question, voice=False)
            else:
                self.emit({"type": "think", "turn_id": turn_id, "note": f"dropped {call.name}"})

    def _emit_perception(self, lane: str, result: Decision | None = None) -> None:
        objects: list[str] = []
        actions: list[str] = []
        if self.scene is not None:
            objects = [f"{item.name}: {item.location_desc}" for item in self.scene.objects_seen]
            for person in self.scene.people:
                actions.extend(item.label for item in person.actions)
        calls: list[dict] = []
        if result is not None:
            calls.extend({"name": call.name, "args": call.args, "kept": True} for call in [*result.meta, *result.device])
            calls.extend({"name": item, "args": {}, "kept": False, "why": "rejected"} for item in result.rejected)
        self.emit({"type": "perception", "lane": lane, "objects": objects, "actions": actions, "calls": calls})

    def _save_moment(self) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        folder = Path("logs/moments") / stamp
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        window = self._window(self.s.decide_span_s, self.s.decide_frames)
        frames = window.frames if window is not None else []
        for index, (jpeg, ts) in enumerate(frames):
            (folder / f"frame_{index}_{ts}.jpg").write_bytes(jpeg)
        if window is not None and window.wav:
            (folder / "audio.wav").write_bytes(window.wav)
        scene = self.scene.model_dump() if self.scene is not None else None
        (folder / "read.json").write_text(json.dumps({"scene": scene}, default=str), encoding="utf-8")
        self.emit({"type": "think", "note": f"saved moment {stamp}"})

    def _open_question(self) -> str | None:
        if self.safety.phase in {"checkin", "checkin_repeat"}:
            return CHECKIN_TEXT
        if self.consent.open is not None:
            return self.consent.open.question
        return None

    def _someone_present(self) -> bool:
        return bool(self.scene and self.scene.people)

    def _say(self, turn_id: str | None, text: str, *, voice: bool) -> None:
        text = text.strip()
        if not text:
            return
        voiced = bool(voice) and self.profile.output.speech
        say_id = self.ids("say")
        self.last_spoken = text
        self.last_spoke_at = self.clock.now()
        self.says[say_id] = {"text": text, "speed": float(self.profile.output.speech_rate)}
        while len(self.says) > 32:
            self.says.popitem(last=False)
        event: dict[str, Any] = {"type": "say", "text": text, "say_id": say_id, "voice": voiced}
        if turn_id:
            event["turn_id"] = turn_id
        self.emit(event)

    def _ask_user(
        self,
        turn_id: str,
        started: float,
        request: DecideRequest,
        question: str,
        choices: list[str] | None,
    ) -> None:
        unprompted = request.kind != "spoken"
        if unprompted:
            hold = self.policy.hold_reason(
                someone_present=self._someone_present(),
                question_open=self._open_question() is not None,
                question=question,
            )
            if hold:
                note = f"would ask: {question}, holding off"
                self.emit({"type": "think", "turn_id": turn_id, "note": note[:160]})
                self._end(turn_id, started, "nothing", note=hold)
                return
            self.policy.note_unprompted()
        self.consent.open_instruction(question, self.profile, unprompted=unprompted)
        self.open_turn = OpenTurn(turn_id)
        self.emit(
            {
                "type": "ask",
                "turn_id": turn_id,
                "question_id": turn_id,
                "question": question,
                "choices": choices or ["Yes", "No"],
            }
        )
        self._say(turn_id, question, voice=True)
        self._end(turn_id, started, "asked")

    def _replace_open_question(self, *, by: str = "voice", said: str = "") -> str:
        """Close a consent or instruction question because the person said something else."""
        if self.safety.phase in {"checkin", "checkin_repeat"}:
            self.policy.note_answered()
            if self.safety_turn and said:
                self.emit({"type": "ask.answer", "turn_id": self.safety_turn, "by": by, "choice": said[:40]})
            return CHECKIN_TEXT
        question = ""
        plan = None
        if self.consent.open is not None:
            question = self.consent.open.question
            plan = self.consent.open.plan
            self.consent.open = None
        turn = self.open_turn
        if turn is not None and said:
            self.emit({"type": "ask.answer", "turn_id": turn.turn_id, "by": by, "choice": said[:40]})
        if plan is not None and turn is not None:
            for call_id in turn.call_ids.values():
                self._tool_status(call_id, "rejected", detail="replaced by their answer")
        self.open_turn = None
        self.policy.note_answered()
        return question

    def _follow_up(self, text: str, question: str, wav: bytes | None) -> None:
        self._request(
            DecideRequest(
                "spoken",
                text[:40],
                set(self.active_cues),
                self.now(),
                wav=wav,
                utterance=text,
                asked=question,
                answer=text,
            )
        )

    # ---------- decide lane ----------

    async def _decide(self, request: DecideRequest) -> None:
        epoch = self.epoch
        turn_id = self.ids("turn")
        started = self.now()
        self.timings.decide_wait_ms = max(0, int((self.now() - request.at) * 1000))
        started_event = {"type": "turn.start", "turn_id": turn_id, "kind": request.kind, "trigger": request.trigger}
        if request.request_id:
            started_event["request_id"] = request.request_id
        self.emit(started_event)
        for cue in sorted(request.cues):
            self.emit({"type": "think", "turn_id": turn_id, "note": f"cue: {_CUE_LABEL.get(cue, cue.replace('_', ' '))}"})
        applicable = self.habits.matching(request.cues, self.scene, self.clock.now())
        window = self._window(self.s.decide_span_s, self.s.decide_frames, wav=request.wav)
        if window is None:
            self._end(turn_id, started, "nothing", note="no frames yet")
            return
        self.emit({"type": "think", "turn_id": turn_id, "note": f"watching {window.seconds:.0f}s · {len(window.frames)} frames"})
        self._budget()
        started_decide = time.perf_counter()
        result = await self.perceiver.decide(
            window,
            profile=self.profile,
            utterance=request.utterance,
            scene=self.scene,
            cues=request.cues,
            effort=self.s.decide_effort,
            asked=request.asked,
            answer=request.answer,
            room_change=request.room_change,
            habit_lines=self.habits.prompt_lines(applicable, self.profile),
            tag_lines=tag_lines(self.world.objects, self.clock.now(), self.profile),
            important=", ".join(sorted(important_for(self.profile))),
            memory=self.world.objects,
            now=self.clock.now(),
        )
        self.timings.decide_ms = int((time.perf_counter() - started_decide) * 1000)
        if not self._live(epoch):
            self._end(turn_id, started, "nothing", note="stopped")
            return
        if self.now() - request.at > self.s.stale_s:
            self._end(turn_id, started, "nothing", note="too late, dropped")
            return
        if result.error:
            self._end(turn_id, started, "nothing", note="perceptron error")
            log.warning("decide failed: %s", result.error)
            return
        self._emit_perception("decide", result)
        if self.delegate_execution:
            self.emit(
                {
                    "type": "inference.status",
                    "stage": "decide",
                    "status": "completed",
                    "turn_id": turn_id,
                    "thinking": result.thinking or "",
                }
            )
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
        plan = complete_paired_request(plan, request.utterance or "")
        reasons = {call.name: call.reason for call in result.device}
        # Falls belong to the safety check-in ("Are you okay?", then the caregiver),
        # not to a second consent question. A model call to alert_caregiver starts
        # the check-in if it is not already running.
        if plan is not None and any(action.tool == "alert_caregiver" for action in plan.actions):
            log.info("alert_caregiver considered/blocked from the decision call")
            why = reasons.get("alert_caregiver") or "looks like a fall"
            self.emit({"type": "think", "turn_id": turn_id, "note": f"considered/blocked: caregiver text ({why})"[:48]})
            confidence = min((call.confidence for call in result.device if call.name == "alert_caregiver"), default=0)
            if confidence >= 0.7 and self.safety.phase in {"idle", "watching"}:
                outcome = self.help.observe(HelpSignal("alert_caregiver", "supporting", "model", self.now()))
                if outcome == "checkin":
                    self._start_help(outcome, trigger="model saw a fall")
            kept = [action for action in plan.actions if action.tool != "alert_caregiver"]
            plan = plan.model_copy(update={"actions": kept}) if kept else None
        if "fall" in request.cues or "fall" in derive(self.scene):
            self._end(turn_id, started, "nothing", note="safety check-in handles this")
            return
        if plan is None and applicable and "fall" not in request.cues:
            habit = applicable[0]
            plan = self.habits.plan_for(
                habit,
                profile=self.profile,
                clock=self.clock,
                ids=self.ids,
                snapshot_id=f"live_{turn_id}",
            )
            reasons = {action.tool: habit_why(habit) for action in plan.actions}
        if plan is None and result.meta:
            await self._run_meta(turn_id, result.meta)
            if result.asked and "left_room" not in request.cues:
                self._ask_user(turn_id, started, request, result.asked, result.choices)
                return
            self._end(turn_id, started, "acted", note="looked where they asked")
            return
        if plan is None:
            if result.asked and "left_room" not in request.cues:
                self._ask_user(turn_id, started, request, result.asked, result.choices)
                return
            if result.reply and "left_room" not in request.cues:
                self._say(turn_id, result.reply, voice=False)
            note = "nobody is in the room" if "left_room" in request.cues else "no tools, nothing needed"
            self._end(turn_id, started, "nothing", note=note)
            return
        pending = self.habits.covering(plan, request.cues, self.scene, self.clock.now())
        update = self.consent.consider(
            plan,
            self.profile,
            transcript_clear=spoken,
            scene=self.scene,
            habits=self.habits,
            cues=request.cues,
        )
        final = update.plan or plan
        if pending is not None and final.consent.given_by == "habit":
            self.emit({"type": "think", "turn_id": turn_id, "note": habit_note(pending)})
            reasons = {action.tool: habit_why(pending) for action in final.actions}
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
        self._call_tools[call_id] = action.tool
        if self.speech_at and self.timings.speech_to_tool_ms is None:
            self.timings.speech_to_tool_ms = max(0, int((self.now() - self.speech_at) * 1000))
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

    def _tool_status(self, call_id: str, status: str, **extra: Any) -> None:
        event: dict[str, Any] = {
            "type": "tool.status",
            "call_id": call_id,
            "status": status,
            "tool": extra.pop("tool", self._call_tools.get(call_id, "")),
        }
        event.update({key: value for key, value in extra.items() if value is not None})
        self.emit(event)

    def _end(self, turn_id: str, started: float, outcome: str, note: str | None = None) -> None:
        if note:
            self.emit({"type": "think", "turn_id": turn_id, "note": note})
        self.emit(
            {
                "type": "turn.end",
                "turn_id": turn_id,
                "ms": int((self.now() - started) * 1000),
                "outcome": outcome,
                "timings": self.timings.as_dict(),
            }
        )

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
                    self._tool_status(call_id, "asking", plan_id=plan.plan_id if plan else None)
                self.emit({"type": "ask", "turn_id": turn.turn_id, "question_id": turn.turn_id, "question": question, "choices": ["Yes", "No"]})
            self._say(turn.turn_id, question, voice=True)
            return "asked"
        status = "declined" if update.status == "declined" else "rejected"
        if update.status == "declined":
            self.emit({"type": "ask.answer", "turn_id": turn.turn_id, "by": by or "none", "choice": "no"})
        for call_id in turn.call_ids.values():
            self._tool_status(call_id, status, detail=update.reason[:60])
        if status == "rejected" and update.reason and update.reason != "replaced by their answer":
            self._say(turn.turn_id, _brief(update.reason), voice=True)
        if self.open_turn is turn:
            self.open_turn = None
        return "nothing"

    async def _answer(self, choice: str, *, by: str) -> None:
        choice = choice.lower()
        if self.safety.phase == "alerted" and choice in {"yes", "okay", "ok"}:
            if self.safety_turn:
                self.emit({"type": "ask.answer", "turn_id": self.safety_turn, "by": by, "choice": "okay"})
            self.policy.note_answered()
            self._safety_effects(self.safety.on_answer("okay"))
            return
        if self.safety.phase in {"checkin", "checkin_repeat"}:
            if choice in {"yes", "okay", "ok"}:
                raw = "okay"
            elif choice in {"help", "need help"}:
                raw = "help"
            else:
                raw = choice
            if self.safety_turn:
                self.emit({"type": "ask.answer", "turn_id": self.safety_turn, "by": by, "choice": raw})
            self.policy.note_answered()
            self._safety_effects(self.safety.on_answer(raw))
            return
        if choice == "help":
            if self.consent.open is not None and self.consent.open.plan is not None:
                self._replace_open_question(by=by, said="help")
            else:
                self.consent.open = None
                self.open_turn = None
                self.policy.note_answered()
            self._safety_effects(self.safety.on_event("distress_call"), trigger="asked for help")
            return
        if self.consent.open is not None and self.consent.open.plan is None:
            self._answer_instruction(choice, by=by)
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
            if update.status in {"approved", "declined"}:
                self.policy.note_answered()
            self._consent_result(update, by=by)

    def _answer_instruction(self, choice: str, *, by: str) -> None:
        question = self.consent.open.question if self.consent.open else ""
        turn = self.open_turn
        self.consent.open = None
        self.open_turn = None
        self.policy.note_answered()
        if self.open_instinct:
            need = self.open_instinct
            self.open_instinct = None
            self._answer_instinct(need, choice, question, turn, by)
            return
        if choice in {"no", "nope", "nah"}:
            self.policy.note_declined_question(question)
            if turn is not None:
                self.emit({"type": "ask.answer", "turn_id": turn.turn_id, "by": by, "choice": "no"})
            return
        word = "yes" if choice in {"yes", "okay", "ok"} else choice
        if turn is not None:
            self.emit({"type": "ask.answer", "turn_id": turn.turn_id, "by": by, "choice": word})
        self._follow_up(word, question, None)

    async def _answer_text(self, text: str, *, by: str) -> bool:
        """Typed text answers an open question, or becomes the next instruction."""
        if self.safety.phase in {"checkin", "checkin_repeat", "alerted"}:
            self._safety_effects(self.safety.on_answer(text))
            if self.safety.phase in {"watching", "idle"} and is_request(text):
                return False
            return True
        if self._open_question() is None:
            return False
        words = text.lower().strip(" .!?")
        if "help me find" in words or "help me look" in words:
            question = self._replace_open_question(by=by, said=text)
            self._follow_up(text, question, None)
            return True
        if words in {"i'm okay", "im okay", "i am okay", "i'm fine", "im fine", "no, i'm fine", "no i'm fine"} or words.startswith(("i'm okay", "im okay", "i am okay", "i'm fine", "no, i'm fine", "no i'm fine")):
            await self._answer("okay", by=by)
            return True
        if self.open_instinct:
            await self._answer(words, by=by)
            return True
        for choice in ("help", "yes", "no", "okay", "ok"):
            if words == choice or words.startswith(choice + " "):
                await self._answer(choice, by=by)
                return True
        question = self._replace_open_question(by=by, said=text)
        self._follow_up(text, question, None)
        return True

    # ---------- doing things ----------

    def _run_plan(self, plan: Plan, turn: OpenTurn) -> None:
        if self.delegate_execution:
            self._handoff_plan(plan, turn)
            return
        for action in plan.actions:
            call_id = turn.call_ids.get(action.action_id) or self._tool_call(turn, action, "")
            self._tool_status(call_id, "approved", plan_id=plan.plan_id, tool=action.tool)
            if action.tool == "alert_caregiver" and self._help_text_sent:
                result = {"ok": True, "delivered": True, "to": "caregiver"}
            else:
                result = run_tool(action.tool, dict(action.args))
                if action.tool == "alert_caregiver" and result.get("delivered"):
                    self._help_text_sent = True
            if result.get("ok", True):
                self._apply(action.tool, action.args, turn.turn_id, result)
                self._tool_status(call_id, "done", plan_id=plan.plan_id, tool=action.tool, result=result)
            else:
                detail = str(result.get("error") or "didn't work")[:60]
                self._tool_status(call_id, "failed", plan_id=plan.plan_id, tool=action.tool, detail=detail, error=detail)

    def _apply(self, tool: str, args: dict[str, Any], turn_id: str | None, result: dict[str, Any] | None = None) -> None:
        state = self.home.apply(tool, args, result)
        if state is not None:
            self.emit(state)
        if tool == "flash_lights":
            token = self.home.flash_token
            self._later(float(args.get("duration_s", 3)), lambda token=token: self._finish_flash(token))
        elif tool == "show_caption":
            text = args.get("text")
            self._later(self.s.caption_s, lambda: self.home.state["caption"] == text and self.emit(self.home.clear_caption()))
        elif tool in {"play_sound", "spotify_play"} and args.get("volume") is not None:
            self._spotify_volume(args.get("volume"))
        elif tool in {"stop_sound", "spotify_pause", "stop_music"}:
            self._pause_spotify()
        elif tool == "announce":
            self._say(turn_id, str(args.get("text", "")), voice=True)

    def _finish_flash(self, token: int) -> None:
        event = self.home.end_flash(token)
        if event is not None:
            self.emit(event)

    def _later(self, seconds: float, fn: Callable[[], Any]) -> None:
        epoch = self.epoch

        async def run() -> None:
            await asyncio.sleep(seconds)
            if epoch == self.epoch:
                fn()

        try:
            task = asyncio.get_running_loop().create_task(run())
        except RuntimeError:
            return
        self.timers.add(task)
        task.add_done_callback(self.timers.discard)

    def _release_ended_fall(self) -> None:
        """A fall that has left the picture ends the incident, so the next one can text."""
        if self.safety.phase == "idle":
            self._fall_absent_since = None
            self._fall_was_here = False
            return
        if "fall" in self.active_cues or self._still_down():
            self._fall_was_here = True
            self._upright_reads = 0
            self.help.saw_down()
            self.safety.evidence_present = True
            return
        if not self._fall_was_here:
            return
        if self._upright_and_moving():
            self._upright_reads += 1
            self.safety.evidence_present = False
            if self.help.note_upright():
                self._fall_was_here = False
                self._upright_reads = 0
                self._safety_effects(self.safety.stand_down())
            return
        self._upright_reads = 0

    def _safety_effects(self, effects: list[SafetyEffect], *, trigger: str = "safety") -> None:
        for effect in effects:
            if self.safety_turn is None:
                self.safety_turn = self.ids("turn")
                self.emit({"type": "turn.start", "turn_id": self.safety_turn, "kind": "safety", "trigger": trigger})
            turn = OpenTurn(self.safety_turn)
            if effect.kind == "checkin":
                self.emit({"type": "safety", "state": SAFETY_UI.get(self.safety.phase, "checkin1"), "status": "checking", "summary": effect.text})
                self.emit({"type": "ask", "turn_id": turn.turn_id, "question_id": turn.turn_id, "question": effect.text, "choices": ["I'm okay", "Need help"]})
                if trigger == "pain":
                    self._checkin_text_sent = False
                    self._pain_text_at = self.now() + PAIN_TEXT_S
                elif trigger != "safety":
                    self._pain_text_at = None
                    self._checkin_text_sent = False
                    self._text_checkin(effect.text)
                else:
                    self._text_checkin(effect.text)
            for plan in effect.plans:
                for action in plan.actions:
                    why = {"alert_caregiver": "no answer or asked for help"}.get(action.tool, "safety check-in")
                    self._tool_call(turn, action, why)
                self._run_plan(plan, turn)
            if effect.kind == "alert":
                self.emit({"type": "safety", "state": "alerted", "status": "alerted", "summary": effect.text})
            elif effect.kind in {"notify_person"} and not effect.plans:
                self._say(turn.turn_id, effect.text, voice=True)
            elif effect.kind == "cleared":
                self._help_text_sent = False
                self._checkin_text_sent = False
                self._pain_text_at = None
                self._say(turn.turn_id, effect.text, voice=True)
                self.emit({"type": "turn.end", "turn_id": turn.turn_id, "ms": 0, "outcome": "nothing"})
                self.emit({"type": "safety", "state": "ok", "status": self.safety.status, "summary": effect.text})
                self.safety_turn = None

    def on_photon_reply(self, sender: str, text: str) -> None:
        """A caregiver texted back. Show it on the phone and as something heard."""
        preview = text.strip()[:80]
        self.home.state["phone"] = {"mode": "reply", "to": sender, "preview": preview, "delivered": True}
        self.emit({"type": "state", "phone": dict(self.home.state["phone"])})
        self.emit({"type": "phone", "from": sender, "text": text.strip()})
        self.emit({"type": "sense.hear", "text": text.strip()})

    async def _classify_sound(self, wav: bytes, epoch: int) -> None:
        """Non-speech labels only. The heard sentence is not a transcript."""

        self.emit({"type": "inference.status", "stage": "sound", "status": "started"})
        read = await self.perceiver.read_sound(wav)
        if not self._live(epoch) or read.error or read.value is None:
            self.emit({"type": "inference.status", "stage": "sound", "status": "unavailable", "detail": "Sound was not classified."})
            return
        allowed = {"clap", "doorbell", "knock", "alarm", "impact", "none"}
        labels = [label for label in read.value.sounds if label in allowed and label != "none"]
        self.emit({"type": "sense.hear", "sound": labels[0] if labels else None, "text": labels[0] if labels else "sound"})
        self.emit({"type": "inference.status", "stage": "sound", "status": "completed", "labels": labels})
        if not labels:
            return
        heard = read.value.model_copy(update={"sounds": labels, "heard": ""})
        scene = apply_sound_read(self.scene, heard) if self.scene is not None else None
        cues = self._scene_cues(scene, set()) if scene is not None else set()
        if "clap" in labels:
            cues.add("clapping")
        self._on_cues(cues, trigger=", ".join(labels))

    def _handoff_plan(self, plan: Plan, turn: OpenTurn) -> None:
        for action in plan.actions:
            call_id = turn.call_ids.get(action.action_id) or self._tool_call(turn, action, "")
            self._tool_status(call_id, "submitted", plan_id=plan.plan_id, detail="Sent to the executor")
        if self.submit_plan is None:
            return
        plan_ref = plan
        turn_id = turn.turn_id

        async def handoff() -> None:
            try:
                await self.submit_plan(plan_ref, turn_id)
            except Exception:
                log.exception("executor handoff failed")
                self.emit({"type": "inference.error", "turn_id": turn_id, "stage": "submit", "message": "The proposal was not submitted to the executor."})

        try:
            task = asyncio.get_running_loop().create_task(handoff())
        except RuntimeError:
            return
        self.timers.add(task)
        task.add_done_callback(self.timers.discard)

    def fresh_window(self) -> Window | None:
        """A copy of the recent frames. Does not wait on the watch task."""

        return self._window(self.s.decide_span_s, self.s.decide_frames)

    def _text_checkin(self, text: str) -> None:
        if self.delegate_execution:
            return
        """One check-in text per incident. The 60-second follow-ups stay in the room."""
        # #region agent log
        import json
        import time
        try:
            with open("/Users/edwinan/Desktop/dubhacks26/.cursor/debug-0f0361.log", "a") as handle:
                handle.write(json.dumps({
                    "sessionId": "0f0361",
                    "hypothesisId": "F",
                    "location": "loop.py:_text_checkin",
                    "message": "check-in text",
                    "data": {"alreadySent": self._checkin_text_sent, "chars": len(text)},
                    "timestamp": int(time.time() * 1000),
                }) + "\n")
        except OSError:
            pass
        # #endregion
        if self._checkin_text_sent:
            return
        if notify_person(text):
            self._checkin_text_sent = True

    def _text_help(self) -> None:
        """One SMS per incident. The later safety alert does not text again."""
        if self.delegate_execution:
            return
        if self._help_text_sent:
            return
        if notify_fall():
            self._help_text_sent = True

    def _thumb(self) -> str | None:
        frame = self.frames.latest
        if frame is None:
            return None
        return "data:image/jpeg;base64," + base64.b64encode(frame.jpeg).decode()


_CUE_LABEL = {"entered_room": "walked in", "left_room": "left"}


def _clap_not_impact(scene: SceneState) -> SceneState:
    """A clap the camera calls a thud is still a clap."""
    text = " ".join([scene.summary, scene.said, *scene.timeline]).lower()
    hands = any(person.gesture == "clapping" for person in scene.people)
    if not hands and "clap" not in scene.sounds and "clap" not in text:
        return scene
    sounds = [label for label in scene.sounds if label not in {"impact", "none"}]
    if "clap" not in sounds:
        sounds.append("clap")
    return scene.model_copy(update={"sounds": sounds})


def _brief(reason: str) -> str:
    text = reason.strip().rstrip(".")
    if len(text) > 80:
        text = text[:77].rstrip() + "..."
    return text


def _chips(scene: SceneState, cues: set[str], tags: list[str] | None = None) -> list[str]:
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
    for cue in sorted(cues):
        chips.append(_CUE_LABEL.get(cue) or f"cue: {cue.replace('_', ' ')}")
    chips.extend(tags or [])
    seen: list[str] = []
    for chip in chips:
        if chip not in seen:
            seen.append(chip)
    return seen[:8]
