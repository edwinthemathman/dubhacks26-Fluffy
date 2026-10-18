"""A normal request must not become a check-in or a caregiver text."""

from datetime import datetime, timedelta, timezone

from fluffy.brain.arbiter import Arbiter
from fluffy.brain.decide import Decider
from fluffy.brain.safety import SafetyMachine
from fluffy.brain.wellbeing import WellbeingInput, assess, is_request, is_strong_phrase
from fluffy.clock import FrozenClock
from fluffy.models import Action, Consent, Evidence, Plan, Trigger, build_plan
from fluffy.perception.provider import Completion, ToolCall
from fluffy.perception.scene import PersonState, SceneState
from fluffy.world.model import WorldModel

from tests.inference.helpers import profiles

NOW = datetime(2026, 10, 18, 1, 0, tzinfo=timezone.utc)
WATER = "Fluffy, can you help me get a bottle of water?"


class _Scripted:
    def __init__(self, calls: list[ToolCall]) -> None:
        self.calls = calls

    async def complete(self, messages, tools):
        names = [tool["function"]["name"] for tool in tools]
        assert "alert_caregiver" not in names
        return Completion(text="", tool_calls=self.calls)


def _person(**kwargs) -> PersonState:
    base = dict(posture="lying", on_floor=True, location_surface="floor", eyes="closed", motion="none")
    base.update(kwargs)
    return PersonState(**base)


def _scenes(clock: FrozenClock, person: PersonState, *, confidence: float = 0.9, span: int = 21) -> list:
    scene = SceneState(confidence=confidence, people=[person], summary="floor")
    return [(clock.now(), scene), (clock.now() + timedelta(seconds=span), scene)]


def test_a_water_request_is_not_distress_and_the_model_cannot_text():
    assert is_request(WATER)
    assert not is_strong_phrase(WATER)
    clock = FrozenClock()
    world = WorldModel(clock)
    standing = SceneState(
        confidence=0.9,
        people=[PersonState(posture="standing", location_surface="unknown")],
        summary="standing",
    )
    assert world.observe_scene(standing) == []
    assert world.observe_transcript(WATER) == []
    assert world.observe_sound("help") == []
    result = assess(
        WellbeingInput(
            transcript=WATER,
            scenes=[(clock.now(), standing)],
            now=clock.now(),
        )
    )
    assert result.status == "request"
    machine = SafetyMachine(clock, profiles()["blind"])
    machine.on_event("possible_fall")
    cleared = machine.on_answer(WATER)
    assert cleared[0].kind == "cleared"
    assert machine.phase == "watching"


def test_floor_play_sofa_tv_and_low_confidence_stay_quiet():
    clock = FrozenClock()
    quiet = [
        _person(motion="small", activity_hint="playing_on_floor", eyes="open"),
        _person(motion="purposeful", activity_hint="picking_up", eyes="open"),
        _person(location_surface="sofa", posture="lying", eyes="closed"),
        _person(location_surface="bed", posture="lying"),
    ]
    for person in quiet:
        found = assess(WellbeingInput(scenes=_scenes(clock, person), now=clock.now() + timedelta(seconds=30)))
        assert found.status in {"fine", "uncertain"}
    low = assess(
        WellbeingInput(
            scenes=_scenes(clock, _person(), confidence=0.2),
            now=clock.now() + timedelta(seconds=30),
        )
    )
    assert low.status == "fine"


def test_still_floor_strong_phrase_and_sos_open_a_checkin_and_only_debilitated_alerts():
    clock = FrozenClock()
    down = assess(
        WellbeingInput(
            scenes=_scenes(clock, _person()),
            now=clock.now() + timedelta(seconds=21),
        )
    )
    assert down.status == "possibly_down"
    assert down.confidence >= 0.85
    phrase = assess(WellbeingInput(transcript="I fell, I can't get up", now=clock.now()))
    assert phrase.status == "possibly_down"
    sos = assess(WellbeingInput(sos=True, now=clock.now()))
    assert sos.status == "debilitated"

    machine = SafetyMachine(clock, profiles()["blind"])
    assert machine.on_event("distress_call")[0].kind == "checkin"
    clock.advance(30)
    assert machine.tick()[0].kind == "checkin"
    clock.advance(30)
    alert = machine.tick()
    assert alert[0].kind == "alert"
    assert "needs help" not in alert[0].text
    clock.advance(60)
    assert machine.tick()[0].kind == "checkin"
    assert machine.on_event("possible_fall") == []

    clock = FrozenClock()
    machine = SafetyMachine(clock, profiles()["blind"])
    effects = machine.on_event("distress_call", source="sos_button")
    assert [effect.kind for effect in effects[:2]] == ["checkin", "alert"]
    assert machine.alerted_once


def test_need_help_alerts_once_and_a_model_alert_is_blocked():
    clock = FrozenClock()
    machine = SafetyMachine(clock, profiles()["blind"])
    machine.on_event("possible_fall")
    effects = machine.on_answer("NEED HELP")
    assert effects[0].kind == "alert"
    assert effects[0].plans[0].actions[0].args["incident_id"].startswith("inc_")
    assert machine.on_answer("NEED HELP") == []

    plan = build_plan(
        clock=clock,
        ids=machine.ids,
        trigger=Trigger(kind="spoken", room="living_room"),
        action_class="safety",
        intent="text them",
        actions=[Action(action_id="act_1", tool="alert_caregiver", args={"severity": "high", "message": "Sam needs help"})],
        confidence=0.99,
        consent=Consent(required=False, given_by="profile"),
        needs=[],
        delivered_via=["speech"],
        reasoning_summary="model",
        evidence=[Evidence(kind="speech", summary="debilitated")],
    )
    verdict = Arbiter(clock).review(plan)
    assert verdict.status == "considered"
    assert "blocked" in verdict.reason or "safety" in verdict.reason


def test_decision_tools_drop_a_caregiver_call():
    clock = FrozenClock()

    async def run():
        decider = Decider(_Scripted([ToolCall(id="1", name="alert_caregiver", arguments='{"severity":"high","message":"Sam needs help"}')]), clock=clock)
        result = await decider.run(
            profile=profiles()["blind"],
            user_text=WATER,
            trigger=Trigger(kind="spoken", utterance=WATER),
            snapshot_id="snap",
        )
        assert result.plan is None
        assert any("alert_caregiver" in item for item in result.rejected)

    import asyncio

    asyncio.run(run())
