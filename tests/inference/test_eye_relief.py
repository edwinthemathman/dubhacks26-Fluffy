"""A held squint eases the light. A blink, a glare, or a bang does not."""

from fluffy.brain.consent import ConsentManager
from fluffy.brain.cues import derive, judge, squint_determined
from fluffy.brain.decide import ensure_eye_relief
from fluffy.clock import FrozenClock
from fluffy.ids import IdFactory
from fluffy.perception.scene import PersonAction, PersonState, SceneState

from tests.inference.helpers import profiles


def _scene(*people: PersonState, sounds: list[str] | None = None) -> SceneState:
    return SceneState(people=list(people), sounds=sounds or ["none"])  # type: ignore[arg-type]


def _squint(count: int) -> PersonState:
    return PersonState(
        posture="sitting",
        eyes="open",
        actions=[PersonAction(label="hold_text_far_or_squint", count=count)],
    )


def test_one_squint_is_not_a_decision():
    scene = _scene(_squint(1))
    assert squint_determined(scene) is False
    assert "glare" in derive(scene)
    assert ensure_eye_relief(None, scene, profile=profiles()["blind"], clock=FrozenClock(), ids=IdFactory()) is None


def test_a_squint_that_stays_dims_the_lights_and_closes_the_blinds():
    scene = _scene(_squint(1))
    plan = ensure_eye_relief(None, scene, profile=profiles()["blind"], clock=FrozenClock(), ids=IdFactory(), held=True)
    assert plan is not None
    tools = {action.tool: action.args for action in plan.actions}
    assert tools["dim_lights"]["luminosity"] == 35
    assert "light" not in tools["dim_lights"]
    assert tools["set_blinds"]["position"] == 0
    assert plan.confidence >= 0.7
    assert judge(scene, plan) == "act"

    update = ConsentManager(FrozenClock()).consider(
        plan,
        profiles()["blind"],
        transcript_clear=False,
        scene=scene,
    )
    assert update.release
    assert update.plan is not None
    assert update.plan.consent.given_by == "cue"


def test_a_hand_over_the_eyes_waits_until_it_stays():
    scene = _scene(PersonState(posture="sitting", gesture="shielding_eyes"))
    profile = profiles()["blind"]
    clock = FrozenClock()
    ids = IdFactory()
    assert ensure_eye_relief(None, scene, profile=profile, clock=clock, ids=ids) is None
    plan = ensure_eye_relief(None, scene, profile=profile, clock=clock, ids=ids, held=True)
    assert plan is not None
    assert {action.tool for action in plan.actions} == {"dim_lights", "set_blinds"}


def test_squinting_twice_in_one_clip_is_enough():
    scene = _scene(_squint(2))
    plan = ensure_eye_relief(None, scene, profile=profiles()["blind"], clock=FrozenClock(), ids=IdFactory())
    assert plan is not None
    assert {action.tool for action in plan.actions} == {"dim_lights", "set_blinds"}


def test_glasses_glare_and_a_loud_sound_change_nothing():
    glare = _scene(PersonState(posture="sitting", wearing_glasses="yes", eyes="open"))
    bang = _scene(PersonState(posture="standing", eyes="open"), sounds=["impact"])
    clock = FrozenClock()
    ids = IdFactory()
    profile = profiles()["blind"]
    assert ensure_eye_relief(None, glare, profile=profile, clock=clock, ids=ids, held=True) is None
    assert ensure_eye_relief(None, bang, profile=profile, clock=clock, ids=ids, held=True) is None
    assert "glare" not in derive(glare)
    assert "fall" not in derive(bang)


def test_a_fall_keeps_the_check_in():
    scene = _scene(
        PersonState(
            fell=True,
            posture="lying",
            on_floor=True,
            location_surface="floor",
            gesture="shielding_eyes",
        )
    )
    assert ensure_eye_relief(None, scene, profile=profiles()["blind"], clock=FrozenClock(), ids=IdFactory(), held=True) is None


def test_opening_the_blinds_does_not_count_as_relief():
    scene = _scene(_squint(2))
    from tests.inference.helpers import dim_plan

    plan = dim_plan(FrozenClock(), luminosity=35)
    plan.actions.append(
        plan.actions[0].model_copy(
            update={"action_id": "act_blinds", "tool": "set_blinds", "args": {"room": "living_room", "position": 100}}
        )
    )
    assert judge(scene, plan) == "ask"
