import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from fluffy.clock import FrozenClock
from fluffy.demo.analyze import _analyze_uploaded
from fluffy.demo.app import app
from fluffy.perception.scene import SceneState, SoundRead, TranscriptRead
from tests.inference.helpers import profiles


def test_clip_page_loads():
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "Watch a clip" in response.text
    assert 'value="speech_cognitive"' in response.text
    assert "Fluffy would do this" in response.text
    assert "Fluffy would ask" in response.text
    assert "Nothing to do" in response.text
    assert "Transcript" in response.text
    assert "How Fluffy decided" in response.text
    assert "Tagged" in response.text


def test_analyze_requires_a_video():
    client = TestClient(app)
    response = client.post("/api/analyze", data={"profile": "blind", "utterance": ""})
    assert response.status_code == 400
    assert "video" in response.json()["detail"].lower()


def _completion(text="", reasoning=None, calls=None):
    tool_calls = None
    if calls:
        tool_calls = [
            SimpleNamespace(id=call["id"], name=call["name"], arguments=call["arguments"])
            for call in calls
        ]
    return SimpleNamespace(
        text=text,
        reasoning=reasoning,
        tool_calls=tool_calls,
        message=SimpleNamespace(content=text, tool_calls=tool_calls),
        usage=None,
    )


class _Completions:
    def __init__(self, replies):
        self.replies = list(replies)

    def create(self, **kwargs):
        if not self.replies:
            raise AssertionError("no scripted completion left")
        return self.replies.pop(0)


class _Client:
    def __init__(self, replies):
        self.chat = SimpleNamespace(completions=_Completions(replies))


def _reads(scene: SceneState, heard: str, sounds: list[str]):
    return [
        _completion(SoundRead(sounds=sounds, heard=heard).model_dump_json()),
        _completion(TranscriptRead(transcript="", clear=False).model_dump_json()),
        _completion(scene.model_dump_json(), reasoning="watched the clip start to end"),
    ]


def test_trace_keeps_scene_and_decision_thinking():
    scene = SceneState(
        timeline=["standing by the sofa", "shields their eyes"],
        people=[{"gesture": "shielding_eyes", "posture": "standing", "activity": "idle"}],
        summary="They are squinting at the lamp.",
        confidence=0.8,
    )
    dim = json.dumps(
        {
            "room": "living_room",
            "luminosity": 30,
            "light": "lamp.living_floor",
            "confidence": 0.9,
            "reason": "shielding eyes",
        }
    )
    client = _Client(
        [
            *_reads(scene, "Quiet room.", ["none"]),
            _completion(
                "Dim the lamp?",
                reasoning="the lamp is too bright for them",
                calls=[
                    {
                        "id": "call_1",
                        "name": "dim_lights",
                        "arguments": dim,
                    }
                ],
            ),
        ]
    )
    result = _analyze_uploaded(
        client,
        "file-abcdefghijklmnopqrstuv",
        profiles()["blind"],
        "",
        clock=FrozenClock(),
    )
    by_step = {entry["step"]: entry for entry in result["trace"]}
    assert by_step["scene"]["thinking"] == "watched the clip start to end"
    assert by_step["scene"]["timeline"] == ["standing by the sofa", "shields their eyes"]
    assert by_step["heard"]["thinking"] is None
    assert by_step["transcript"]["thinking"] is None
    decide = by_step["decide"]
    assert decide["round"] == 1
    assert decide["thinking"] == "the lamp is too bright for them"
    assert decide["tool_calls"] == [
        {
            "name": "dim_lights",
            "args": {
                "room": "living_room",
                "luminosity": 30,
                "light": "lamp.living_floor",
            },
            "reason": "shielding eyes",
            "confidence": 0.9,
        }
    ]
    assert "fallback" not in by_step


def test_trace_fallback_when_a_fall_gets_no_tool():
    scene = SceneState(
        timeline=["standing", "drops out of view"],
        people=[{"fell": True, "posture": "lying", "activity": "fallen", "on_floor": True, "location_surface": "floor"}],
        sounds=["impact"],
        summary="They are on the floor.",
        confidence=0.7,
    )
    client = _Client(
        [
            *_reads(scene, "A heavy impact, then quiet.", ["impact"]),
            _completion("", reasoning="I am not sure what to do"),
        ]
    )
    result = _analyze_uploaded(
        client,
        "file-abcdefghijklmnopqrstuv",
        profiles()["blind"],
        "",
        clock=FrozenClock(),
    )
    steps = [entry["step"] for entry in result["trace"]]
    assert steps == ["heard", "transcript", "scene", "cues", "decide", "fallback"]
    assert "fall" in result["trace"][3]["cues"]
    assert result["trace"][2]["thinking"] == "watched the clip start to end"
    assert result["trace"][4]["thinking"] == "I am not sure what to do"
    assert result["trace"][4]["tool_calls"] == []
    assert result["trace"][5]["status"] == "checking"
    assert "alert_caregiver" not in result["trace"][5]["summary"]
