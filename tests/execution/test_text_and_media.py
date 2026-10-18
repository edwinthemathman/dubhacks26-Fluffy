"""Text decisions and media intake stay local and do not call a provider."""

import asyncio
import base64
from types import SimpleNamespace

from fastapi.testclient import TestClient

from fluffy.config import load_settings
from fluffy.live.perceive import Window, decide, decide_text
from fluffy.live.text_bridge import PerceptronTextInterpreter, submit_text
from fluffy.profile.model import Agreements, InputSettings, OutputSettings, Profile, QuestionSettings
from fluffy.server.app import create_harness_app
from fluffy.server.runtime import build_runtime


class _Completion:
    def __init__(self, text="", tool_calls=None):
        self.text = text
        self.tool_calls = tool_calls or []
        self.message = None


class _FakeClient:
    def __init__(self, completion):
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.completion = completion

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.completion


def _profile() -> Profile:
    return Profile(
        id="speech_cognitive",
        display_name="Sam",
        needs=["speech"],
        vocabulary=["lamp"],
        output=OutputSettings(speech=True, captions=True),
        input=InputSettings(voice=True, typing=True, buttons=True),
        questions=QuestionSettings(confirm_back="always", style="one_at_a_time"),
        agreements=Agreements(caregiver="Alex", person_name="Sam"),
    )


def test_decide_text_sends_words_and_no_media():
    client = _FakeClient(_Completion(text="The camera is needed."))

    result = asyncio.run(decide_text(client, profile=_profile(), utterance="dim the lamp"))

    assert result.reply == "The camera is needed."
    assert client.calls and len(client.calls) == 1
    sent = client.calls[0]
    assert sent["messages"][1]["content"].startswith("Evidence mode: transcript_only.")
    assert "dim the lamp" in sent["messages"][1]["content"]
    assert isinstance(sent["messages"][1]["content"], str)
    names = {tool["function"]["name"] for tool in sent["tools"]}
    assert "dim_lights" in names
    assert "describe_scene" not in names
    assert "look" not in names


def test_configured_interpreter_imports_decide_text():
    client = _FakeClient(_Completion())
    interpreter = PerceptronTextInterpreter(client, _profile())
    decision = asyncio.run(interpreter.interpret("hello"))
    assert decision.error is None
    assert decision.actions == []


def test_turn_starts_before_a_slow_interpreter():
    events = []

    class Runtime:
        stopped = False
        idempotent = {}
        settings = SimpleNamespace(session_id="ses_demo")

        async def emit(self, session_id, event_type, payload):
            del session_id
            events.append((event_type, payload))

    class Slow:
        async def interpret(self, text, window=None):
            assert any(kind == "turn.start" for kind, _ in events)
            from fluffy.live.text_bridge import TextDecision

            del text, window
            return TextDecision(error="Perceptron is not configured. The words were not turned into a device action.")

    result = asyncio.run(submit_text(Runtime(), request_id="req_slow1", text="dim the lamp", interpreter=Slow(), explicit=True))
    assert events[0][0] == "turn.start"
    assert events[0][1]["request_id"] == "req_slow1"
    assert result["status"] == "unavailable"
    assert events[-1][0] == "turn.end"


def test_visual_decision_includes_frames_and_no_audio():
    client = _FakeClient(_Completion(text=""))
    window = Window(frames=[(b"\xff\xd8\xff\xd9", 0), (b"\xff\xd8\xff\xd9", 1000)], wav=b"not-audio", seconds=1)
    asyncio.run(decide(client, window, profile=_profile(), utterance="", scene=None, cues=set()))
    content = client.calls[0]["messages"][1]["content"]
    assert isinstance(content, list)
    assert all(part != "not-audio" and not (isinstance(part, dict) and part.get("type") == "audio") for part in content)


def test_delegated_plan_does_not_call_run_tool(monkeypatch):
    from fluffy.live.loop import LiveLoop, OpenTurn

    def fail(*args, **kwargs):
        del args, kwargs
        raise AssertionError("run_tool")

    monkeypatch.setattr("fluffy.live.loop.run_tool", fail)
    loop = LiveLoop(object(), {"speech_cognitive": _profile()}, profile_id="speech_cognitive", delegate_execution=True)
    events = []
    loop.emit = events.append
    before = loop.home.state["lamp"]["luminosity"]
    plan = SimpleNamespace(
        plan_id="pln_1",
        actions=[SimpleNamespace(action_id="act_1", tool="dim_lights", args={"luminosity": 10})],
    )
    loop._run_plan(plan, OpenTurn(turn_id="trn_1", call_ids={"act_1": "call_1"}))
    assert loop.home.state["lamp"]["luminosity"] == before
    assert events[-1]["status"] == "submitted"


def test_one_frame_does_not_make_a_window():
    from fluffy.live.loop import LiveLoop

    class Perceiver:
        async def read_scene(self, *args, **kwargs):
            raise AssertionError("scene read")

        async def read_sound(self, *args, **kwargs):
            raise AssertionError("sound read")

        async def answer(self, *args, **kwargs):
            raise AssertionError("answer")

        async def transcript(self, *args, **kwargs):
            raise AssertionError("transcript")

        async def read_tags(self, *args, **kwargs):
            raise AssertionError("tags")

        async def decide(self, *args, **kwargs):
            raise AssertionError("decide")

    loop = LiveLoop(Perceiver(), {"speech_cognitive": _profile()}, profile_id="speech_cognitive", delegate_execution=True)
    loop.on_frame(1, b"\xff\xd8\xff\xd9", 0.1)
    assert loop.fresh_window() is None


def test_frame_route_accepts_jpeg_without_claiming_analysis(tmp_path):
    settings = load_settings(
        environ={
            "FLUFFY_SESSION_ID": "ses_media",
            "FLUFFY_HOME_PORT": "9",
            "FLUFFY_AUTOTICK": "0",
            "FLUFFY_DATA": str(tmp_path),
        }
    )
    runtime = build_runtime(settings)
    app = create_harness_app(settings, runtime)
    jpeg = base64.b64encode(b"\xff\xd8\xff\xd9").decode()
    with TestClient(app) as client:
        bad = client.post("/api/media/frames", json={"session_id": "ses_media", "epoch": 1, "ts_ms": 1, "motion": 0, "jpeg_base64": "!!!!"})
        assert bad.status_code == 400
        ok = client.post(
            "/api/media/frames",
            json={"session_id": "ses_media", "epoch": 1, "ts_ms": 10, "motion": 0.1, "jpeg_base64": jpeg},
        )
        assert ok.status_code == 202
        assert ok.json()["analyzed"] is False
        stale = client.post(
            "/api/media/frames",
            json={"session_id": "ses_media", "epoch": 0, "ts_ms": 11, "motion": 0, "jpeg_base64": jpeg},
        )
        assert stale.status_code == 400
        health = client.get("/api/health").json()
        assert "perceptron" not in str(health).lower() or "api_key" not in str(health)
        assert health["media"]["frames_received"] == 1
        silent = base64.b64encode(b"\x00\x00" * 1600).decode()
        audio = client.post(
            "/api/media/audio",
            json={"session_id": "ses_media", "epoch": 1, "pcm16_base64": silent, "sample_rate": 16000, "format": "pcm16"},
        )
        assert audio.status_code == 202
        assert audio.json()["classified"] is False
        assert client.get("/api/health").json()["media"]["transcripts"] == 0


class _ToolCall:
    def __init__(self, name: str, arguments: str) -> None:
        self.id = "c1"
        self.name = name
        self.arguments = arguments


def test_text_decision_is_one_response():
    import json

    completion = _Completion(
        text="Which lamp?",
        tool_calls=[_ToolCall("ask_user", json.dumps({"question": "Which lamp?"}))],
    )
    completion.message = {"role": "assistant", "content": "again"}
    client = _FakeClient(completion)
    result = asyncio.run(decide_text(client, profile=_profile(), utterance="dim a lamp"))
    assert len(client.calls) == 1
    assert result.rounds == 1
    assert result.asked == "Which lamp?"
    assert result.device == []


def test_browser_transcript_with_frames_does_not_attach_audio():
    client = _FakeClient(_Completion(text=""))
    window = Window(frames=[(b"\xff\xd8\xff\xd9", 0), (b"\xff\xd8\xff\xd9", 1000)], wav=b"raw-audio", seconds=1)
    interpreter = PerceptronTextInterpreter(client, _profile())
    decision = asyncio.run(interpreter.interpret("dim the lamp", window=window))
    assert decision.evidence == "video_plus_transcript"
    content = client.calls[0]["messages"][1]["content"]
    assert isinstance(content, list)
    assert "video_plus_transcript" in content[-1]["text"]
    assert "dim the lamp" in content[-1]["text"]
    assert "raw-audio" not in str(content)
    assert all(not (isinstance(part, dict) and part.get("type") == "audio") for part in content)


def test_uncertain_decision_asks_and_does_not_submit(tmp_path):
    from fluffy.live.text_bridge import ProposedAction, TextDecision

    runtime, submitted = _spy_runtime(tmp_path)
    decision = TextDecision(
        actions=[ProposedAction("dim_lights", {"room": "living_room", "luminosity": 20}, reason="maybe dim")],
        confidence=0.4,
        reply="Should I dim the lamp?",
        thinking="long chain of thought that must stay off the tool",
    )
    result = asyncio.run(
        submit_text(runtime, request_id="req_unsure1", text="maybe dim the lamp", interpreter=_Fixed(decision), explicit=False)
    )
    assert submitted == []
    assert result["status"] == "question"
    assert runtime.questions
    assert "chain of thought" not in runtime.questions[result["question_id"]].reasoning_summary


def test_certain_decision_submits_one_plan(tmp_path):
    from fluffy.live.text_bridge import ProposedAction, TextDecision

    runtime, submitted = _spy_runtime(tmp_path)
    decision = TextDecision(
        actions=[ProposedAction("dim_lights", {"room": "living_room", "luminosity": 20}, reason="asked to dim")],
        confidence=0.9,
        reply="Dim the lamp.",
        thinking="long chain of thought that must stay off the tool",
    )
    result = asyncio.run(
        submit_text(runtime, request_id="req_sure01", text="dim the lamp", interpreter=_Fixed(decision), explicit=False)
    )
    assert len(submitted) == 1
    assert result["status"] == "submitted"
    plan = submitted[0]
    assert plan.reasoning_summary == "asked to dim"
    assert plan.actions[0].tool == "dim_lights"
    assert "confidence" not in plan.actions[0].args
    assert "chain of thought" not in plan.reasoning_summary
    assert "chain of thought" not in str(plan.actions[0].args)


def test_open_door_submits_instead_of_unknown_tool(tmp_path):
    from datetime import datetime, timezone

    from fluffy.ids import new_id
    from fluffy.models.contracts import Observation, PlanSubmission

    settings = _settings(tmp_path)
    runtime = build_runtime(settings)
    observation = Observation(
        observation_id=new_id("obs"),
        session_id=settings.session_id,
        kind="speech",
        captured_at=datetime.now(timezone.utc),
        room="living_room",
        supports=["explicit_request"],
        targets=["front_door"],
        summary="open the front door",
        source="live",
    )
    runtime.remember_observation(observation)
    submission = PlanSubmission(
        schema_version="2.0",
        session_id=settings.session_id,
        intent="Open the front door",
        trigger={"kind": "speech", "event": "explicit_request", "room": "living_room", "utterance": "open the front door"},
        actions=[
            {
                "action_id": new_id("act"),
                "tool": "open_door",
                "args": {"door": "front_door"},
                "evidence_ids": [observation.observation_id],
            }
        ],
        reasoning_summary="asked to open",
    )
    result = asyncio.run(runtime.submit(submission, explicit=True))
    assert result.get("error_code") != "unknown_tool"
    assert result["status"] == "submitted"
    assert runtime.queued[0]["plan"]["actions"][0]["tool"] == "open_door"


def test_browser_speech_is_refused_out_loud_unless_capture_is_active(tmp_path):
    from fluffy.live.text_bridge import UnavailableInterpreter

    settings = _settings(tmp_path)
    runtime = build_runtime(settings)
    app = create_harness_app(settings, runtime, interpreter=UnavailableInterpreter())
    jpeg = base64.b64encode(b"\xff\xd8\xff\xd9").decode()
    body = {
        "session_id": settings.session_id,
        "request_id": "req_speech1",
        "text": "dim the lamp",
        "epoch": 1,
        "final": True,
        "source": "browser_speech",
        "captured_at": "2026-10-18T08:00:00Z",
    }
    with TestClient(app) as client:
        refused = client.post("/api/input/transcript", json=body)
        assert refused.status_code == 409
        assert "not listening" in refused.json()["detail"]["message"].lower()
        for ts in (10, 20):
            frames = client.post(
                "/api/media/frames",
                json={"session_id": settings.session_id, "epoch": 1, "ts_ms": ts, "motion": 0.1, "jpeg_base64": jpeg},
            )
            assert frames.status_code == 202
        accepted = client.post("/api/input/transcript", json=body)
        assert accepted.status_code == 202
        stale = client.post("/api/input/transcript", json={**body, "request_id": "req_speech2", "epoch": 0})
        assert stale.status_code == 400
        assert stale.json()["detail"]["error_code"] == "stale_epoch"


class _Fixed:
    def __init__(self, decision) -> None:
        self.decision = decision

    async def interpret(self, text, window=None):
        del text, window
        return self.decision


def _settings(tmp_path):
    return load_settings(
        environ={
            "FLUFFY_SESSION_ID": "ses_words",
            "FLUFFY_HOME_PORT": "9",
            "FLUFFY_AUTOTICK": "0",
            "FLUFFY_DATA": str(tmp_path),
        }
    )


def _spy_runtime(tmp_path):
    runtime = build_runtime(_settings(tmp_path))
    submitted = []

    async def spy(submission, explicit, mode_override=None):
        del explicit, mode_override
        submitted.append(submission)
        return {"status": "submitted", "plan_id": "pln_spy"}

    runtime.submit = spy
    return runtime, submitted
