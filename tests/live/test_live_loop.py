"""The live loop, offline: a fake Perceptron and a frozen clock."""

import asyncio
import io
import json
import struct
import wave

from fastapi.testclient import TestClient

from fluffy.clock import FrozenClock
from fluffy.live.buffers import AudioBuffer, FrameBuffer, SpeechGate, to_wav
from fluffy.live.home import HomeState
from fluffy.live.loop import LiveLoop, LiveSettings
from fluffy.live.perceive import Decision, Read
from fluffy.live.server import FRAME_HEADER, create_app, handle_binary
from fluffy.models.tool_catalog import parse_call
from fluffy.perception.scene import PersonState, SceneState, SoundRead

from tests.inference.helpers import profiles

EVENT_TYPES = {
    "state", "profile", "turn.start", "sense.see", "sense.hear", "think", "tool.call",
    "tool.status", "ask", "ask.answer", "say", "turn.end", "turn.raw", "safety", "fallback",
}

CALM = SceneState(people=[PersonState(posture="sitting", location_surface="sofa", eyes="open")], summary="sitting")
FALLEN = SceneState(
    timeline=["standing", "falls to floor", "stays down"],
    people=[PersonState(fell=True, posture="lying", on_floor=True, location_surface="floor")],
    summary="on the floor",
)
DOZING = SceneState(
    people=[PersonState(posture="lying", activity="sleeping", eyes="closed", location_surface="sofa")],
    summary="dozing on the sofa",
)


class FakePerceiver:
    def __init__(self) -> None:
        self.scene = CALM
        self.sound = SoundRead(sounds=["none"], heard="quiet")
        self.answer_value = "unclear"
        self.transcript_text = "just a little dimmer"
        self.asked: str | None = None
        self.choices: list[str] | None = None
        self.device: list[str] = []
        self.scene_calls = 0
        self.sound_calls = 0
        self.windows: list = []
        self.decide_calls: list[dict] = []
        self.decide_gate: asyncio.Event | None = None

    async def read_scene(self, window, *, effort, profile=None):
        self.scene_calls += 1
        self.windows.append(window)
        return Read(value=self.scene, ms=900)

    async def read_sound(self, wav):
        self.sound_calls += 1
        return Read(value=self.sound, ms=300)

    async def answer(self, wav, question):
        return Read(value=self.answer_value, ms=200)

    async def transcript(self, wav, profile):
        from fluffy.perception.scene import TranscriptRead

        return Read(value=TranscriptRead(transcript=self.transcript_text), ms=200)

    async def decide(self, window, *, profile, utterance, scene, cues, effort, asked="", answer=""):
        self.decide_calls.append({"utterance": utterance, "cues": set(cues), "asked": asked, "answer": answer})
        if self.decide_gate is not None:
            await self.decide_gate.wait()
        return Decision(
            device=[parse_call(name, args) for name, args in self.device],
            asked=self.asked,
            choices=self.choices,
            ms=800,
        )


def dim(level: int) -> tuple[str, str]:
    return (
        "dim_lights",
        json.dumps({"room": "living_room", "luminosity": level, "light": "lamp.living_floor",
                    "confidence": 0.9, "reason": "dozing, lamp bright"}),
    )


def make(profile: str = "blind", **settings):
    clock = FrozenClock()
    fake = FakePerceiver()
    events: list[dict] = []
    loop = LiveLoop(fake, profiles(), profile_id=profile, clock=clock, emit=events.append,
                    settings=LiveSettings(**settings))
    return loop, fake, clock, events


def feed_frames(loop: LiveLoop, clock: FrozenClock, seconds: float = 2.0, motion: float = 0.0) -> None:
    for i in range(int(seconds * 4)):
        loop.on_frame(int(clock.now().timestamp() * 1000), b"jpeg", motion)
        clock.advance(0.25)


async def settle(loop: LiveLoop, rounds: int = 3) -> None:
    for _ in range(rounds):
        await loop.tick()
        for _ in range(10):
            await asyncio.sleep(0)


def types(events: list[dict]) -> list[str]:
    return [event["type"] for event in events]


# ---------- buffers ----------

def test_frame_buffer_stays_bounded_when_timestamps_stall():
    buf = FrameBuffer()
    for i in range(500):
        buf.add(1000, bytes([i % 256]), 0.0, at=float(i))
    assert len(buf.frames) == buf.max_frames
    assert buf.frames[-1].jpeg == bytes([499 % 256])


def test_an_older_flash_does_not_turn_off_a_newer_one():
    home = HomeState()
    home.apply("flash_lights", {"color": "amber", "pattern": "pulse1"})
    first = home.flash_token
    home.apply("flash_lights", {"color": "blue", "pattern": "strobe"})
    assert home.end_flash(first) is None
    assert home.state["flash"]["active"] is True
    assert home.state["flash"]["color"] == "blue"
    done = home.end_flash(home.flash_token)
    assert done is not None and done["flash"]["active"] is False


def test_frame_window_is_evenly_spread_and_starts_at_zero():
    buf = FrameBuffer()
    for i in range(40):  # 10 s at 4 fps
        buf.add(i * 250, bytes([i]), 0.0, at=float(i))
    window = buf.window(4.0, 8)
    assert len(window) == 8
    stamps = [ms for _, ms in window]
    assert stamps[0] == 0 and stamps == sorted(stamps)
    assert stamps[-1] == 4000
    assert window[-1][0] == bytes([39])


def test_frames_never_go_back_in_time():
    buf = FrameBuffer()
    buf.add(1000, b"a", 0, 0)
    buf.add(900, b"b", 0, 0)
    assert [f.ts_ms for f in buf.frames] == [1000, 1000]


def test_wav_header_is_valid():
    audio = AudioBuffer()
    audio.add(struct.pack("<1600h", *([1000] * 1600)))
    with wave.open(io.BytesIO(to_wav(audio.last(0.1)))) as handle:
        assert handle.getframerate() == 16000
        assert handle.getnframes() == 1600


def test_speech_gate_returns_one_burst_after_quiet():
    gate = SpeechGate()
    end, found = 0, []
    for rms in [0] * 5 + [3000] * 10 + [0] * 10:  # 100 ms chunks
        end += 1600
        burst = gate.feed(rms, end, 1600)
        if burst:
            found.append(burst)
    assert len(found) == 1
    start, stop = found[0]
    assert start < 5 * 1600 + 1600 and stop > 15 * 1600 - 1600


# ---------- lanes ----------

def test_motion_then_settle_fires_one_scene_read():
    async def run():
        loop, fake, clock, _ = make(heartbeat_s=999)
        loop.last_watch = clock.now().timestamp()
        feed_frames(loop, clock, 1.0, motion=0.2)
        await settle(loop)
        assert fake.scene_calls == 0  # still moving
        clock.advance(0.6)
        await settle(loop)
        assert fake.scene_calls == 1
        clock.advance(0.6)
        await settle(loop)
        assert fake.scene_calls == 1  # nothing new moved
    asyncio.run(run())


def test_a_fall_starts_the_checkin_before_the_decision_returns():
    async def run():
        loop, fake, clock, events = make()
        fake.scene = FALLEN
        fake.decide_gate = asyncio.Event()  # the decision call never returns in this test
        feed_frames(loop, clock)
        await settle(loop)
        assert {"type": "safety", "state": "checkin1"} in events
        asks = [e for e in events if e["type"] == "ask"]
        assert asks and asks[0]["question"] == "Are you okay?"
        assert any(e["type"] == "tool.call" and e["tool"] == "flash_lights" for e in events)
        assert len(fake.decide_calls) == 1  # queued and running, not finished
        fake.decide_gate.set()
    asyncio.run(run())


def test_a_sound_label_from_a_silent_window_is_ignored():
    async def run():
        loop, fake, clock, events = make()
        fake.scene = SceneState(sounds=["impact"], summary="a ball bounces")  # nobody in view
        feed_frames(loop, clock)
        loop.on_audio(struct.pack("<8000h", *([0] * 8000)))  # half a second of silence
        await settle(loop)
        assert loop.scene is not None and loop.scene.sounds == ["none"]
        assert fake.windows and fake.windows[-1].wav is None
        assert not any(e["type"] == "safety" for e in events)
        assert fake.decide_calls == []
    asyncio.run(run())


def test_a_loud_window_keeps_its_audio():
    async def run():
        loop, fake, clock, _ = make(heartbeat_s=999)
        loop.last_watch = clock.now().timestamp()
        feed_frames(loop, clock, 1.0, motion=0.2)
        loop.on_audio(struct.pack("<8000h", *([8000] * 8000)))
        clock.advance(0.6)
        await settle(loop)
        assert fake.windows and fake.windows[-1].wav is not None
    asyncio.run(run())


def test_a_repeated_fall_is_latched():
    async def run():
        loop, fake, clock, events = make(heartbeat_s=1)
        fake.scene = FALLEN
        feed_frames(loop, clock)
        await settle(loop)
        for _ in range(4):
            clock.advance(1.5)
            feed_frames(loop, clock, 0.5)
            await settle(loop)
        assert fake.scene_calls >= 3
        # One check-in turn plus one decision turn, and no repeats while it stays latched.
        checkins = [e for e in events if e["type"] == "turn.start" and e["trigger"] == "fall seen"]
        assert len(checkins) == 1
        assert len(fake.decide_calls) == 1
        assert len([e for e in events if e["type"] == "ask"]) == 1
    asyncio.run(run())


def test_a_newer_trigger_replaces_a_queued_one_and_stale_ones_drop():
    async def run():
        loop, fake, clock, _ = make(stale_s=10)
        feed_frames(loop, clock)
        loop.last_watch = clock.now().timestamp() + 999  # no scene reads in this test
        fake.decide_gate = asyncio.Event()
        await loop.on_client({"type": "typed", "text": "first"})
        await settle(loop)  # "first" is now running and blocked
        await loop.on_client({"type": "typed", "text": "second"})
        await loop.on_client({"type": "typed", "text": "third"})
        fake.decide_gate.set()
        await settle(loop)
        assert [c["utterance"] for c in fake.decide_calls] == ["first", "third"]

        fake.decide_gate = asyncio.Event()
        await loop.on_client({"type": "typed", "text": "old"})
        clock.advance(11)
        await settle(loop)
        assert "old" not in [c["utterance"] for c in fake.decide_calls]
    asyncio.run(run())


def test_reset_discards_a_decision_still_in_flight():
    async def run():
        loop, fake, clock, events = make()
        fake.device = [dim(20)]
        fake.decide_gate = asyncio.Event()
        feed_frames(loop, clock)
        loop.last_watch = clock.now().timestamp() + 999
        await loop.on_client({"type": "typed", "text": "dim the lamp"})
        await settle(loop)
        assert len(fake.decide_calls) == 1
        await loop.on_client({"type": "room.reset"})
        fake.decide_gate.set()
        await settle(loop)
        assert loop.consent.open is None
        assert loop.home.state["lamp"]["luminosity"] == 78
        assert any(event["type"] == "think" and event["note"] == "stopped" for event in events)
        assert not any(event["type"] == "tool.status" and event["status"] == "done" for event in events)
    asyncio.run(run())


def _loud_then_quiet(loop: LiveLoop) -> None:
    loud = struct.pack("<1600h", *([4000] * 1600))
    quiet = struct.pack("<1600h", *([0] * 1600))
    for _ in range(4):
        loop.on_audio(loud)
    for _ in range(8):
        loop.on_audio(quiet)


def test_a_burst_during_a_sound_read_is_not_dropped():
    async def run():
        loop, fake, _, _ = make()
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow(_wav):
            fake.sound_calls += 1
            started.set()
            await release.wait()
            return Read(value=fake.sound, ms=300)

        fake.read_sound = slow
        _loud_then_quiet(loop)
        await started.wait()
        _loud_then_quiet(loop)
        assert fake.sound_calls == 1
        release.set()
        await settle(loop)
        assert fake.sound_calls == 2
    asyncio.run(run())


def test_a_comfort_offer_asks_then_a_yes_changes_the_room():
    async def run():
        loop, fake, clock, events = make(profile="speech_cognitive")
        fake.scene = DOZING
        fake.device = [dim(20)]
        feed_frames(loop, clock)
        await settle(loop, rounds=4)
        assert any(e["type"] == "ask" for e in events) or any(
            e["type"] == "tool.status" and e["status"] == "done" for e in events
        )
        if any(e["type"] == "ask" for e in events):
            await loop.on_client({"type": "answer", "choice": "yes", "by": "button"})
        states = [e for e in events if e["type"] == "state" and "lamp" in e]
        assert states and states[-1]["lamp"]["luminosity"] == 20
        assert any(e["type"] == "tool.status" and e["status"] == "done" for e in events)
    asyncio.run(run())


def test_every_event_is_a_known_web_event():
    async def run():
        loop, fake, clock, events = make()
        fake.scene = FALLEN
        feed_frames(loop, clock)
        await settle(loop, rounds=4)
        await loop.on_client({"type": "answer", "choice": "help", "by": "button"})
        await loop.on_client({"type": "caregiver.ack"})
        assert set(types(events)) <= EVENT_TYPES
        assert "alerted" in [e.get("state") for e in events if e["type"] == "safety"]
        assert any(e["type"] == "tool.call" and e["tool"] == "alert_caregiver" for e in events)
    asyncio.run(run())


def test_a_spoken_yes_answers_the_checkin():
    async def run():
        loop, fake, clock, events = make()
        fake.scene = FALLEN
        feed_frames(loop, clock)
        await settle(loop)
        fake.answer_value = "yes"
        await loop._hear(0, 1600)
        assert {"type": "safety", "state": "ok"} in events
        assert loop.safety.phase == "watching"
    asyncio.run(run())


# ---------- server ----------

def test_binary_frames_and_audio_reach_the_loop():
    loop, _, _, _ = make()
    handle_binary(loop, FRAME_HEADER.pack(1, 1234, 0.25) + b"\xff\xd8jpeg")
    handle_binary(loop, bytes([2]) + struct.pack("<160h", *([0] * 160)))
    assert loop.frames.latest.ts_ms == 1234
    assert loop.frames.latest.jpeg == b"\xff\xd8jpeg"
    assert abs(loop.frames.latest.motion - 0.25) < 1e-6
    assert loop.audio.end == 160


def test_ui_socket_sends_profile_and_room_on_connect():
    def factory(emit):
        loop, *_ = make()
        loop.emit = emit
        return loop

    with TestClient(create_app(loop_factory=factory)) as client:
        with client.websocket_connect("/ws/ui") as socket:
            first = socket.receive_json()
            second = socket.receive_json()
    assert first["type"] == "profile" and first["profile"] == "blind"
    assert second["type"] == "state" and "lamp" in second


def test_sound_labels_fall_back_to_the_description():
    from fluffy.live.perceive import labels_from_text

    assert labels_from_text("a doorbell rings") == ["doorbell"]
    assert labels_from_text("a loud thud, then someone groans") == ["pain", "impact"]
    assert labels_from_text("someone says help me find my keys") == ["speech"]
    assert labels_from_text("quiet room tone") == []
    assert labels_from_text("they are falling asleep") == []
    assert labels_from_text("that was helpful") == []
    assert labels_from_text("he fell") == ["impact"]
