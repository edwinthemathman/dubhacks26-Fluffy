"""A throat scratch becomes a water sentence, and a clutch does not."""

from fluffy.brain.instincts import Instincts
from fluffy.clock import FrozenClock
from fluffy.perception.scene import ObjectSighting, PersonAction, PersonState, SceneState
from fluffy.world.model import ObjectMemory

from tests.inference.helpers import profiles


def _guess(scene, memory, now):
    book = Instincts(anchors=("the window", "the door"))
    book.note(scene, now)
    book.note(scene, now)
    return book.guess(
        scene,
        memory,
        profiles()["elderly"],
        now,
        music_on=False,
        spoke_recently=False,
        last_line="",
    )


def test_throat_scratch_and_a_clear_name_the_bottle():
    clock = FrozenClock()
    memory = ObjectMemory()
    memory.note_live(
        [ObjectSighting(name="water bottle", location_desc="on the desk", side="right")],
        clock.now(),
    )
    scene = SceneState(
        people=[
            PersonState(
                posture="standing",
                facing="side",
                wearing_glasses="no",
                expression="discomfort",
                actions=[PersonAction(label="rub_throat", count=2)],
            )
        ],
        sounds=["throat_clear"],
    )
    guess = _guess(scene, memory, clock.now())
    assert guess is not None
    assert guess.text == "Your water bottle is on the right side of the desk."
    assert "saw:" in guess.chain and "pattern:" in guess.chain and "found:" in guess.chain


def test_a_clutch_with_pain_asks_if_they_are_okay():
    clock = FrozenClock()
    scene = SceneState(
        people=[
            PersonState(
                expression="pain",
                actions=[PersonAction(label="clutch_throat", count=1)],
            )
        ],
        objects_seen=[ObjectSighting(name="water bottle", location_desc="on the desk", side="right")],
    )
    guess = _guess(scene, ObjectMemory(), clock.now())
    assert guess is not None
    assert guess.text == "Are you okay?"
    assert "water" not in guess.text.lower()


def test_lost_glasses_are_named_even_when_wearing_is_unclear():
    clock = FrozenClock()
    memory = ObjectMemory()
    memory.note_live(
        [ObjectSighting(name="glasses", location_desc="on the table", side="right")],
        clock.now(),
    )
    scene = SceneState(
        people=[
            PersonState(
                wearing_glasses="unclear",
                expression="confused",
                facing="side",
                actions=[
                    PersonAction(label="touch_face_eyes", count=2),
                    PersonAction(label="pat_chest_pocket", count=2),
                ],
            )
        ]
    )
    guess = _guess(scene, memory, clock.now())
    assert guess is not None
    assert guess.text == "Your glasses are on the right side of the table."


def test_a_drink_does_not_match_glasses():
    memory = ObjectMemory()
    clock = FrozenClock()
    memory.note_live([ObjectSighting(name="glasses", location_desc="on the table")], clock.now())
    assert memory.recall("glass", clock.now()).status == "unknown"
    assert memory.recall("water", clock.now()).status == "unknown"
