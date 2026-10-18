"""The live loop runs device tools itself and the harness talks to that loop."""

from types import SimpleNamespace

from fastapi.testclient import TestClient

from fluffy.config import load_settings
from fluffy.live.harness_bridge import HarnessPerception
from fluffy.live.loop import LiveLoop, OpenTurn
from fluffy.profile.model import Agreements, InputSettings, OutputSettings, Profile, QuestionSettings
from fluffy.server.app import create_harness_app
from fluffy.server.runtime import build_runtime


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


def _loop():
    events: list[dict] = []
    loop = LiveLoop(object(), {"speech_cognitive": _profile()}, profile_id="speech_cognitive", emit=events.append)
    return loop, events


def _settings(tmp_path):
    return load_settings(
        environ={
            "FLUFFY_SESSION_ID": "ses_direct",
            "FLUFFY_HOME_PORT": "9",
            "FLUFFY_AUTOTICK": "0",
            "FLUFFY_DATA": str(tmp_path),
        }
    )


def test_harness_builds_a_loop_that_runs_tools(tmp_path):
    settings = _settings(tmp_path)
    perception = HarnessPerception.build(build_runtime(settings), settings)
    assert perception.loop.delegate_execution is False
    assert perception.loop.submit_plan is None
    assert perception.loop.on_clap.__name__ == "play_clap_song"


def test_an_approved_plan_runs_the_tool_and_reports_it(monkeypatch):
    loop, events = _loop()
    seen = []

    def fake(tool, args):
        seen.append((tool, dict(args)))
        return {"ok": True}

    monkeypatch.setattr("fluffy.live.loop.run_tool", fake)
    plan = SimpleNamespace(
        plan_id="pln_1",
        actions=[
            SimpleNamespace(
                action_id="act_1",
                tool="dim_lights",
                args={"room": "living_room", "luminosity": 20, "light": "lamp.living_floor"},
            )
        ],
    )
    loop._run_plan(plan, OpenTurn("turn_1"))
    assert seen == [("dim_lights", {"room": "living_room", "luminosity": 20, "light": "lamp.living_floor"})]
    kinds = [event["type"] for event in events]
    assert kinds.index("tool.call") < kinds.index("tool.status")
    done = next(event for event in events if event["type"] == "tool.status" and event["status"] == "done")
    assert done["tool"] == "dim_lights"
    assert done["result"] == {"ok": True}
    assert any(event["type"] == "state" and event.get("lamp", {}).get("luminosity") == 20 for event in events)


def test_alert_caregiver_texts_through_notify_fall(monkeypatch):
    loop, _events = _loop()
    calls = []

    def fake(message=None, sms=None, to=None):
        del sms, to
        calls.append(message)
        return True

    monkeypatch.setattr("fluffy.integrations.notify_fall", fake)
    plan = SimpleNamespace(
        plan_id="pln_help",
        actions=[SimpleNamespace(action_id="act_help", tool="alert_caregiver", args={"message": "Sam needs help"})],
    )
    loop._run_plan(plan, OpenTurn("turn_help"))
    assert calls == ["Sam needs help"]


def test_typed_words_and_answers_reach_the_loop(tmp_path):
    settings = _settings(tmp_path)
    app = create_harness_app(settings, build_runtime(settings))
    seen = []

    async def capture(message):
        seen.append(message)

    with TestClient(app) as client:
        app.state.perception.loop.on_client = capture
        typed = client.post(
            "/api/input/text",
            json={"session_id": settings.session_id, "request_id": "req_dim01", "text": "turn on the lights"},
        )
        assert typed.status_code == 202
        answered = client.post("/api/questions/q_loop/answer", json={"answer": "Yes", "source": "button"})
        assert answered.status_code == 200
        assert answered.json()["status"] == "forwarded"
        other = client.post("/api/questions/q_loop/answer", json={"answer": "glasses", "source": "button"})
        assert other.status_code == 200
        home = client.get("/api/state")
        assert home.status_code == 200
        body = home.json()
        assert body["type"] == "state"
        assert body["state"]["lamp"]["luminosity"] == 78
    assert seen[0] == {"type": "typed", "text": "turn on the lights", "request_id": "req_dim01"}
    assert seen[1] == {"type": "answer", "choice": "yes", "by": "button"}
    assert seen[2]["type"] == "typed"
    assert seen[2]["text"] == "glasses"
