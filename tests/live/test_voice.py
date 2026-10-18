"""ElevenLabs voice, when Fluffy speaks, and open answers. All offline."""

import asyncio
import json
import struct

import httpx
from fastapi.testclient import TestClient

from fluffy.clock import FrozenClock
from fluffy.demo.analyze import _user_text
from fluffy.live.loop import DecideRequest
from fluffy.live.server import create_app
from fluffy.live.voice_policy import VoicePolicy
from fluffy.voice.elevenlabs import DEFAULT_MODEL, DEFAULT_VOICE_ID, ElevenLabsVoice
from tests.inference.helpers import profiles
from tests.live.test_live_loop import DOZING, FALLEN, feed_frames, make, settle


def test_elevenlabs_request_shape_and_cache():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert request.url.path == f"/v1/text-to-speech/{DEFAULT_VOICE_ID}/stream"
        assert request.url.params["output_format"] == "mp3_44100_128"
        assert request.headers["xi-api-key"] == "test-key"
        body = json.loads(request.content)
        assert body["model_id"] == DEFAULT_MODEL
        assert body["voice_settings"]["speed"] == 0.85
        assert body["text"] == "Are you okay?"
        return httpx.Response(200, content=b"mp3-bytes")

    async def run():
        voice = ElevenLabsVoice("test-key", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        first = await voice.audio("Are you okay?", 0.85)
        second = await voice.audio("Are you okay?", 0.85)
        assert first == b"mp3-bytes" and second == first
        assert calls["n"] == 1
        await voice.aclose()

    asyncio.run(run())


def test_elevenlabs_401_or_timeout_means_unavailable():
    def denied(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "no"})

    def slow(_request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow")

    async def run():
        denied_voice = ElevenLabsVoice("k", client=httpx.AsyncClient(transport=httpx.MockTransport(denied)))
        assert await denied_voice.audio("Hi", 1.0) is None
        assert denied_voice.available is False

        slow_voice = ElevenLabsVoice("k", client=httpx.AsyncClient(transport=httpx.MockTransport(slow)))
        assert await slow_voice.audio("Hi", 1.0) is None
        assert slow_voice.available is False

    asyncio.run(run())


def test_missing_voice_returns_204():
    def factory(emit):
        loop, *_ = make()
        loop.emit = emit
        loop.says["say_1"] = {"text": "Are you okay?", "speed": 1.0}
        return loop

    with TestClient(create_app(loop_factory=factory)) as client:
        assert client.get("/api/voice/say_1").status_code == 204
        assert client.get("/api/voice/missing").status_code == 204


def test_voice_endpoint_streams_cached_audio():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"mp3-ok")

    def factory(emit):
        loop, *_ = make()
        loop.emit = emit
        loop.says["say_9"] = {"text": "Hello", "speed": 1.0}
        return loop

    def voice_factory():
        return ElevenLabsVoice("k", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    with TestClient(create_app(loop_factory=factory, voice_factory=voice_factory)) as client:
        response = client.get("/api/voice/say_9")
    assert response.status_code == 200
    assert response.content == b"mp3-ok"
    assert response.headers["content-type"].startswith("audio/mpeg")


def test_deaf_profile_is_never_voiced():
    async def run():
        loop, fake, clock, events = make(profile="deaf")
        fake.scene = DOZING
        fake.asked = "Should I dim the lamp?"
        feed_frames(loop, clock)
        await settle(loop, rounds=4)
        says = [event for event in events if event["type"] == "say"]
        assert says and all(event["voice"] is False for event in says)

    asyncio.run(run())


def test_safety_checkin_is_voiced_right_after_another_question():
    async def run():
        loop, fake, clock, events = make()
        loop.policy.note_unprompted()
        fake.scene = FALLEN
        feed_frames(loop, clock)
        await settle(loop)
        says = [event for event in events if event["type"] == "say" and event["text"] == "Are you okay?"]
        assert says and says[0]["voice"] is True

    asyncio.run(run())


def test_unprompted_questions_wait_three_minutes_and_after_two_silences():
    clock = FrozenClock()
    policy = VoicePolicy(clock)
    clear = dict(someone_present=True, question_open=False, question="Dim the lamp?")
    assert policy.hold_reason(**clear) is None
    policy.note_unprompted()
    assert "3 minutes" in (policy.hold_reason(**clear) or "")
    clock.advance(3 * 60 + 1)
    assert policy.hold_reason(**clear) is None

    policy.note_unanswered()
    policy.note_unanswered()
    assert "unanswered" in (policy.hold_reason(**clear) or "")
    clock.advance(10 * 60 + 1)
    assert policy.hold_reason(**clear) is None

    policy.note_declined_question("Dim the lamp?")
    assert "declined" in (policy.hold_reason(**clear) or "")
    assert policy.hold_reason(someone_present=False, question_open=False, question="Other?") == "nobody in view"


def test_model_ask_user_becomes_a_spoken_question_then_holds_off():
    async def run():
        loop, fake, clock, events = make()
        fake.scene = DOZING
        fake.asked = "Want the lamp a little dimmer?"
        feed_frames(loop, clock)
        await settle(loop, rounds=4)
        asks = [event for event in events if event["type"] == "ask" and "dimmer" in event["question"]]
        says = [event for event in events if event["type"] == "say" and event["text"] == fake.asked]
        assert asks and says and says[0]["voice"] is True
        assert says[0]["say_id"] in loop.says

        before = len([event for event in events if event["type"] == "ask"])
        loop._ask_user(
            "turn_extra",
            loop.now(),
            DecideRequest("offer", "again", set(), loop.now()),
            "Want the lamp a little dimmer?",
            None,
        )
        assert len([event for event in events if event["type"] == "ask"]) == before
        assert any(event["type"] == "think" and "holding off" in event["note"] for event in events)

    asyncio.run(run())


def test_other_answer_queues_a_spoken_decide_and_closes_the_plan():
    async def run():
        loop, fake, clock, events = make(profile="speech_cognitive")
        fake.scene = DOZING
        fake.device = [
            (
                "dim_lights",
                json.dumps(
                    {
                        "room": "living_room",
                        "luminosity": 40,
                        "light": "lamp.living_floor",
                        "confidence": 0.5,
                        "reason": "maybe the lamp",
                    }
                ),
            )
        ]
        feed_frames(loop, clock)
        await settle(loop, rounds=4)
        assert loop.consent.open is not None and loop.consent.open.plan is not None
        question = loop.consent.open.question
        fake.answer_value = "other"
        fake.transcript_text = "just a little dimmer"
        fake.device = []
        await loop._hear(0, 3200)
        await settle(loop)
        assert any(
            event["type"] == "tool.status" and event.get("detail") == "replaced by their answer" for event in events
        )
        follow = fake.decide_calls[-1]
        assert follow["utterance"] == "just a little dimmer"
        assert follow["asked"] == question
        assert follow["answer"] == "just a little dimmer"
        assert loop.consent.open is None

    asyncio.run(run())


def test_typed_open_answer_is_an_instruction():
    async def run():
        loop, fake, clock, events = make()
        fake.scene = DOZING
        fake.asked = "Want the lamp a little dimmer?"
        feed_frames(loop, clock)
        await settle(loop, rounds=4)
        assert loop.consent.open is not None and loop.consent.open.plan is None
        fake.asked = None
        await loop.on_client({"type": "typed", "text": "just a little dimmer"})
        await settle(loop)
        follow = fake.decide_calls[-1]
        assert follow["utterance"] == "just a little dimmer"
        assert follow["asked"] == "Want the lamp a little dimmer?"
        assert loop.consent.open is None

    asyncio.run(run())


def test_tts_start_mutes_until_end_plus_a_tail():
    async def run():
        loop, _, clock, _ = make()
        await loop.on_client({"type": "tts.start"})
        loop.on_audio(struct.pack("<1600h", *([3000] * 1600)))
        assert loop.audio.end == 0
        await loop.on_client({"type": "tts.end"})
        loop.on_audio(struct.pack("<1600h", *([3000] * 1600)))
        assert loop.audio.end == 0
        clock.advance(0.4)
        loop.on_audio(struct.pack("<1600h", *([3000] * 1600)))
        assert loop.audio.end == 1600

    asyncio.run(run())


def test_answer_context_is_added_to_the_decision_text():
    text = _user_text(
        profiles()["blind"],
        "just a little dimmer",
        None,
        set(),
        asked="Want the lamp a little dimmer?",
        answer="just a little dimmer",
    )
    assert 'Fluffy just asked: "Want the lamp a little dimmer?". The person answered: "just a little dimmer".' in text


def test_prompt_asks_when_unsure():
    from fluffy.brain.decide import system_prompt

    prompt = system_prompt(profiles()["blind"])
    assert "call ask_user with one short, friendly question" in prompt
