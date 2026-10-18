"""Standing habits: load them, match them, and let them act without a question."""

from pathlib import Path

import pytest

from fluffy.brain.consent import ConsentManager
from fluffy.brain.habits import habit_why, load_habits
from fluffy.clock import FrozenClock
from fluffy.ids import IdFactory
from fluffy.models.tool_catalog import ToolError
from fluffy.perception.scene import PersonState, SceneState

from tests.inference.helpers import dim_plan, profiles

ROOT = Path(__file__).resolve().parents[2]
HABITS = ROOT / "config" / "habits.toml"


def _book():
    return load_habits(HABITS)


def _dark():
    return SceneState(
        people=[PersonState(posture="standing", activity="idle")],
        lighting="dark",
        sounds=["none"],
    )


def test_loading_rejects_bad_tool_arguments(tmp_path: Path):
    path = tmp_path / "habits.toml"
    path.write_text(
        """
[[habit]]
id = "bad"
said = "Turn the lights past the end of the dial."
when = "entered_room"
actions = [{ tool = "dim_lights", args = { room = "living_room", luminosity = 140 } }]
"""
    )
    with pytest.raises(ToolError):
        load_habits(path)


def test_walk_in_habit_approves_at_half_confidence_without_asking():
    clock = FrozenClock()
    book = _book()
    scene = _dark()
    update = ConsentManager(clock).consider(
        dim_plan(clock, confidence=0.55, luminosity=80, intent="Turn the lights on"),
        profiles()["blind"],
        transcript_clear=False,
        scene=scene,
        habits=book,
        cues={"entered_room"},
    )
    assert update.release
    assert update.question is None
    assert update.plan is not None
    assert update.plan.consent.given_by == "habit"


def test_the_same_plan_asks_when_no_habit_applies():
    clock = FrozenClock()
    update = ConsentManager(clock).consider(
        dim_plan(clock, confidence=0.55, luminosity=80, intent="Turn the lights on"),
        profiles()["blind"],
        transcript_clear=False,
        scene=_dark(),
        cues={"entered_room"},
    )
    assert update.status == "asking"
    assert update.release is False
    assert update.question == "Should I turn the lights on?"


def test_backup_acts_when_the_model_proposes_nothing():
    clock = FrozenClock()
    book = _book()
    scene = _dark()
    habit = book.matching({"entered_room"}, scene, clock.now())[0]
    plan = book.plan_for(habit, profile=profiles()["blind"], clock=clock, ids=IdFactory())
    update = ConsentManager(clock).consider(
        plan,
        profiles()["blind"],
        transcript_clear=False,
        scene=scene,
        habits=book,
        cues={"entered_room"},
    )
    assert update.release
    assert update.plan is not None
    assert update.plan.consent.given_by == "habit"
    assert update.plan.actions[0].args["luminosity"] == 80
    assert habit_why(habit) == "habit: lights on when I walk in"
    assert book.prompt_lines([habit], profiles()["blind"]) == [
        "Sam's habit: when they walk in, lights on (80%)."
    ]


def test_a_declined_offer_is_not_overridden():
    clock = FrozenClock()
    book = _book()
    manager = ConsentManager(clock)
    blind = profiles()["blind"]
    plan = dim_plan(clock, confidence=0.55, luminosity=80, intent="Turn the lights on")
    asked = manager.consider(plan, blind, transcript_clear=False, scene=_dark())
    assert asked.status == "asking"
    assert manager.answer_button(False) is not None
    clock.advance(60)
    again = manager.consider(
        plan,
        blind,
        transcript_clear=False,
        scene=_dark(),
        habits=book,
        cues={"entered_room"},
    )
    assert again.status == "dropped"
    assert again.release is False
    assert "declined" in again.reason


def test_a_habit_cools_down_after_it_acts():
    clock = FrozenClock()
    book = _book()
    scene = _dark()
    habit = book.matching({"entered_room"}, scene, clock.now())[0]
    book.note_used(habit, clock.now())
    clock.advance(30)
    assert book.matching({"entered_room"}, scene, clock.now()) == []
    clock.advance(90)
    assert book.matching({"entered_room"}, scene, clock.now()) == [habit]


def test_left_room_never_asks_and_the_lights_off_habit_acts():
    clock = FrozenClock()
    book = _book()
    blind = profiles()["blind"]
    scene = SceneState(people=[], lighting="normal", sounds=["none"])
    unrelated = ConsentManager(clock).consider(
        dim_plan(clock, confidence=0.9, luminosity=50, intent="Dim the lights a bit"),
        blind,
        transcript_clear=False,
        scene=scene,
        habits=book,
        cues={"left_room"},
    )
    assert unrelated.status == "dropped"
    assert unrelated.question is None
    assert unrelated.reason == "nobody is in the room"

    clock = FrozenClock()
    book = _book()
    off = ConsentManager(clock).consider(
        dim_plan(clock, confidence=0.55, luminosity=0, intent="Turn the lights off"),
        blind,
        transcript_clear=False,
        scene=scene,
        habits=book,
        cues={"left_room"},
    )
    assert off.release
    assert off.question is None
    assert off.plan is not None
    assert off.plan.consent.given_by == "habit"


def test_a_bright_room_does_not_turn_the_walk_in_lights_on():
    book = _book()
    bright = SceneState(people=[PersonState(posture="standing")], lighting="bright")
    assert book.matching({"entered_room"}, bright, FrozenClock().now()) == []
