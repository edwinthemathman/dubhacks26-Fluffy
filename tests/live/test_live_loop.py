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
    "phone", "perception", "still",
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
        self.meta: list = []
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
        self.decide_calls.append({"utterance": utterance, "cues": set(cues), "asked": asked, "answer": answer})
        if self.decide_gate is not None:
            await self.decide_gate.wait()
        return Decision(
            device=[parse_call(name, args) for name, args in self.device],
            meta=list(self.meta),
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


def make(profile: str = "blind", habits=None, on_clap=None, on_volume=None, on_pause=None, **settings):
    clock = FrozenClock()
    fake = FakePerceiver()
    events: list[dict] = []
    loop = LiveLoop(
        fake,
        profiles(),
        profile_id=profile,
        clock=clock,
        emit=events.append,
        settings=LiveSettings(**settings),
        habits=habits,
        on_clap=on_clap,
        on_volume=on_volume,
        on_pause=on_pause,
    )
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


async def see_down(loop: LiveLoop, fake: FakePerceiver, clock: FrozenClock) -> None:
    """Two confident, still floor reads past the dwell. That is the check-in bar."""
    fake.scene = SceneState(
        confidence=0.95,
        people=[PersonState(posture="lying", on_floor=True, location_surface="floor", eyes="closed", motion="none")],
        summary="still on the floor",
    )
    feed_frames(loop, clock)
    await settle(loop)
    clock.advance(21)
    feed_frames(loop, clock, 0.5)
    await settle(loop)


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
        fake.scene = SceneState(
            confidence=0.95,
            people=[PersonState(posture="lying", on_floor=True, location_surface="floor", eyes="closed", motion="none")],
            summary="still on the floor",
        )
        fake.decide_gate = asyncio.Event()  # the decision call never returns in this test
        feed_frames(loop, clock)
        await settle(loop)
        assert not any(event["type"] == "safety" for event in events)
        clock.advance(21)
        feed_frames(loop, clock, 0.5)
        await settle(loop)
        assert any(event["type"] == "safety" and event.get("state") == "checkin1" for event in events)
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


def test_quiet_speech_still_goes_out_with_the_frames():
    async def run():
        loop, fake, clock, _ = make(heartbeat_s=999)
        loop.last_watch = clock.now().timestamp()
        feed_frames(loop, clock, 1.0, motion=0.2)
        loop.on_audio(struct.pack("<8000h", *([200] * 8000)))
        clock.advance(0.6)
        await settle(loop)
        assert fake.windows and fake.windows[-1].wav is not None
    asyncio.run(run())


def test_words_heard_in_the_scene_become_a_spoken_decision():
    async def run():
        loop, fake, clock, _ = make()
        fake.scene = SceneState(
            people=[PersonState(posture="sitting", location_surface="sofa")],
            sounds=["speech"],
            said="play some music",
            summary="asking for music",
        )
        feed_frames(loop, clock)
        loop.on_audio(struct.pack("<8000h", *([2000] * 8000)))
        await settle(loop, rounds=4)
        assert any(call["utterance"] == "play some music" for call in fake.decide_calls)
    asyncio.run(run())


def test_glare_has_to_stay_before_it_asks():
    async def run():
        loop, fake, clock, events = make(heartbeat_s=1, glare_hold_s=8)
        fake.scene = SceneState(
            people=[PersonState(gesture="shielding_eyes", posture="standing")],
            lighting="bright",
            summary="hand over their eyes",
        )
        feed_frames(loop, clock)
        await settle(loop)
        sees = [event for event in events if event["type"] == "sense.see"]
        assert sees and "cue: glare" not in sees[-1]["chips"]
        assert fake.decide_calls == []
        clock.advance(9)
        feed_frames(loop, clock, 0.5)
        await settle(loop)
        assert any(event["type"] == "sense.see" and "cue: glare" in event["chips"] for event in events)
        assert fake.decide_calls and "glare" in fake.decide_calls[-1]["cues"]

    asyncio.run(run())


def test_glare_in_a_dim_room_does_not_ask():
    async def run():
        loop, fake, clock, events = make(heartbeat_s=1, glare_hold_s=8)
        loop.home.state["lamp"]["luminosity"] = 30
        loop.home.state["ceiling"]["luminosity"] = 30
        fake.scene = SceneState(
            people=[PersonState(gesture="shielding_eyes", posture="standing")],
            lighting="bright",
            summary="hand over their eyes",
        )
        feed_frames(loop, clock)
        await settle(loop)
        clock.advance(9)
        feed_frames(loop, clock, 0.5)
        await settle(loop)
        assert fake.decide_calls == []
        assert not any(event["type"] == "sense.see" and "cue: glare" in event["chips"] for event in events)

    asyncio.run(run())


def test_glare_stays_quiet_for_ten_minutes_after_it_runs():
    async def run():
        loop, fake, clock, _ = make(heartbeat_s=1, glare_hold_s=8)
        fake.scene = SceneState(
            people=[PersonState(gesture="shielding_eyes", posture="standing")],
            lighting="bright",
            summary="hand over their eyes",
        )
        feed_frames(loop, clock)
        await settle(loop)
        clock.advance(9)
        feed_frames(loop, clock, 0.5)
        await settle(loop)
        assert len(fake.decide_calls) == 1
        clock.advance(40)
        feed_frames(loop, clock, 0.5)
        await settle(loop)
        assert len(fake.decide_calls) == 1

    asyncio.run(run())


def test_walking_in_runs_the_lights_on_habit_without_asking():
    async def run():
        from fluffy.brain.habits import load_habits
        from tests.inference.test_habits import HABITS

        loop, fake, clock, events = make(heartbeat_s=1, habits=load_habits(HABITS))
        fake.scene = SceneState(people=[], lighting="dark", summary="empty")
        feed_frames(loop, clock)
        await settle(loop)
        clock.advance(6)
        feed_frames(loop, clock, 0.5)
        await settle(loop)
        fake.scene = SceneState(
            people=[PersonState(posture="walking", activity="arriving")],
            lighting="dark",
            summary="walked in",
        )
        clock.advance(1)
        feed_frames(loop, clock, 0.5)
        await settle(loop)
        calls = [event for event in events if event["type"] == "tool.call" and event["tool"] == "dim_lights"]
        assert calls
        assert calls[-1]["args"]["luminosity"] == 80
        assert calls[-1]["why"] == "habit: lights on when I walk in"
        assert any(event["type"] == "think" and event["note"].startswith("habit applies:") for event in events)
        assert not any(event["type"] == "ask" for event in events)

    asyncio.run(run())


def test_a_repeated_fall_is_latched():
    async def run():
        loop, fake, clock, events = make(heartbeat_s=1)
        await see_down(loop, fake, clock)
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


def test_chrome_speech_is_the_utterance_the_decision_hears():
    async def run():
        loop, fake, clock, events = make()
        feed_frames(loop, clock)
        loop.last_watch = clock.now().timestamp() + 999
        await loop.on_client({"type": "speech", "text": "it's getting late, put on some music"})
        await settle(loop)
        assert any(event["type"] == "sense.hear" and event["text"] == "it's getting late, put on some music" for event in events)
        assert fake.decide_calls[-1]["utterance"] == "it's getting late, put on some music"
        # The same words on the next scene read must not start a second turn.
        fake.scene = SceneState(summary="sitting", said="it's getting late, put on some music")
        clock.advance(1)
        loop.last_watch = 0
        await settle(loop)
        assert len(fake.decide_calls) == 1

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


def test_turn_end_carries_stage_timings():
    async def run():
        loop, fake, clock, events = make(profile="speech_cognitive")
        fake.scene = DOZING
        fake.device = [dim(20)]
        feed_frames(loop, clock)
        await settle(loop, rounds=4)
        ends = [event for event in events if event["type"] == "turn.end"]
        assert ends
        timings = ends[-1]["timings"]
        assert set(timings) == {
            "frame_age_ms",
            "scene_read_ms",
            "decide_wait_ms",
            "decide_ms",
            "speech_to_tool_ms",
        }

    asyncio.run(run())


def test_every_event_is_a_known_web_event():
    async def run():
        loop, fake, clock, events = make()
        await see_down(loop, fake, clock)
        await loop.on_client({"type": "answer", "choice": "help", "by": "button"})
        await loop.on_client({"type": "caregiver.ack"})
        assert set(types(events)) <= EVENT_TYPES
        assert "alerted" in [e.get("state") for e in events if e["type"] == "safety"]
        assert any(e["type"] == "tool.call" and e["tool"] == "alert_caregiver" for e in events)
    asyncio.run(run())


def test_a_spoken_yes_answers_the_checkin():
    async def run():
        loop, fake, clock, events = make()
        await see_down(loop, fake, clock)
        fake.answer_value = "yes"
        await loop._hear(0, 1600)
        assert any(event["type"] == "safety" and event.get("state") == "ok" for event in events)
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


def test_closing_the_feed_pauses_and_drops_frames():
    holder: dict = {}

    def factory(emit):
        loop, *_ = make()
        loop.emit = emit
        holder["loop"] = loop
        return loop

    with TestClient(create_app(loop_factory=factory)) as client:
        with client.websocket_connect("/ws/live") as socket:
            socket.send_bytes(FRAME_HEADER.pack(1, 1234, 0.5) + b"jpeg-bytes")
            # send_bytes can return before the server task reads the frame.
            for _ in range(20):
                if holder["loop"].frames.latest is not None:
                    break
                client.get("/api/health")
            assert holder["loop"].paused is False
            assert holder["loop"].frames.latest.jpeg == b"jpeg-bytes"
    assert holder["loop"].paused is True
    assert holder["loop"].frames.latest is None


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
    assert labels_from_text("two sharp claps") == ["clap"]
    assert labels_from_text("hands clapping, a loud bang") == ["clap"]
    assert labels_from_text("they are falling asleep") == []
    assert labels_from_text("that was helpful") == []
    assert labels_from_text("he fell") == ["impact"]


def _clap_player():
    played: list[str] = []

    def on_clap() -> dict:
        played.append("go")
        return {
            "ok": True,
            "track": "Should I Stay or Should I Go - Remastered",
            "artist": "The Clash",
            "device": "Kelton's MacBook Pro",
        }

    return played, on_clap


def test_a_clap_is_not_an_impact():
    from fluffy.live.loop import _clap_not_impact

    thud = _clap_not_impact(SceneState(sounds=["impact"], summary="a ball hits the floor"))
    assert thud.sounds == ["impact"]
    clap = _clap_not_impact(
        SceneState(
            sounds=["impact"],
            people=[PersonState(gesture="clapping")],
            summary="they clap",
        )
    )
    assert clap.sounds == ["clap"]
    named = _clap_not_impact(SceneState(sounds=["impact", "speech"], said="that was a clap"))
    assert "impact" not in named.sounds
    assert "clap" in named.sounds


def test_clapping_plays_the_song_without_asking():
    played, on_clap = _clap_player()
    loop, _, clock, events = make(on_clap=on_clap)
    loop._on_cues({"clapping"}, trigger="clapping")
    assert played == ["go"]
    call = next(event for event in events if event["type"] == "tool.call")
    assert call["tool"] == "spotify_play"
    assert "Should I Stay" in call["args"]["track"]
    assert not any(event["type"] == "ask" for event in events)
    loop._on_cues({"clapping"}, trigger="clapping")
    assert played == ["go"]
    clock.advance(30)
    loop._on_cues({"clapping"}, trigger="clapping")
    assert played == ["go"]
    loop.reset()
    loop._on_cues({"clapping"}, trigger="clapping")
    assert played == ["go", "go"]


def test_a_seen_clap_plays_the_song():
    async def run():
        played, on_clap = _clap_player()
        loop, fake, clock, events = make(on_clap=on_clap)
        fake.scene = SceneState(
            people=[PersonState(gesture="clapping", posture="standing")],
            summary="they clap",
        )
        feed_frames(loop, clock)
        await settle(loop)
        assert played == ["go"]
        assert any(event.get("tool") == "spotify_play" for event in events)
        await settle(loop)
        assert played == ["go"]

    asyncio.run(run())


def test_volume_control_sets_spotify_volume():
    seen: list[int] = []

    def on_volume(level: int) -> dict:
        seen.append(level)
        return {"ok": True, "volume": level}

    loop, _, _, events = make(on_volume=on_volume)
    asyncio.run(loop.on_client({"type": "volume.set", "volume": 35}))
    assert seen == [35]
    assert loop.home.state["speaker"]["volume"] == 35
    assert events[-1]["speaker"]["volume"] == 35
    loop._apply("play_sound", {"sound": "music", "room": "living_room", "volume": 20}, None)
    assert seen == [35, 20]


def test_a_stop_cue_pauses_once_and_does_not_ask():
    paused: list[str] = []

    def on_pause() -> dict:
        paused.append("pause")
        return {"ok": True}

    loop, _, _, events = make(on_pause=on_pause)
    loop.home.apply("play_sound", {"sound": "Should I Stay or Should I Go - Remastered", "volume": 40})
    loop._on_cues({"stop_music"}, trigger="stop")
    assert paused == ["pause"]
    assert loop.home.state["speaker"]["sound"] is None
    assert any(event.get("tool") == "spotify_pause" for event in events)
    assert not any(event["type"] == "ask" for event in events)
    loop._on_cues({"stop_music"}, trigger="stop")
    assert paused == ["pause"]


def test_saying_stop_pauses_the_song():
    async def run():
        paused: list[str] = []
        loop, fake, _, _ = make(on_pause=lambda: paused.append("pause") or {"ok": True})
        loop.home.apply("play_sound", {"sound": "Should I Stay or Should I Go - Remastered", "volume": 40})
        await loop._heard_speech("stop the music")
        assert paused == ["pause"]
        assert fake.decide_calls == []

    asyncio.run(run())


def test_saying_louder_changes_spotify_volume():
    async def run():
        seen: list[int] = []
        loop, fake, _, _ = make(on_volume=lambda level: seen.append(level) or {"ok": True, "volume": level})
        loop.home.state["speaker"]["volume"] = 40
        await loop._heard_speech("turn the volume up")
        assert seen == [60]
        assert loop.home.state["speaker"]["volume"] == 60
        await loop._heard_speech("volume 25")
        assert seen == [60, 25]
        assert fake.decide_calls == []

    asyncio.run(run())
