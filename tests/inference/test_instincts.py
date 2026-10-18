"""Gesture patterns become one spoken guess, or stay quiet."""

from datetime import timedelta

from fluffy.brain.instincts import Instincts, place_phrase, speakable
from fluffy.clock import FrozenClock
from fluffy.live.verify import confirm
from fluffy.perception.scene import ObjectSighting, PersonAction, PersonState, SceneState
from fluffy.world.model import ObjectMemory

from tests.inference.helpers import profiles


def _person(**kwargs):
    base = dict(
        posture="standing",
        face_visible="yes",
        wearing_glasses="no",
        on_phone="no",
        speaking="no",
        zone="table",
        facing="unclear",
    )
    base.update(kwargs)
    return PersonState(**base)


def _acts(*labels, count=1):
    return [PersonAction(label=label, count=count) for label in labels]


def _sight(name, place, **flags):
    return ObjectSighting(name=name, location_desc=place, **flags)


def _book():
    return Instincts(anchors=("the window", "the door"))


def _guess(scene, memory, now, book=None, **flags):
    book = book or _book()
    book.note(scene, now)
    return book.guess(
        scene,
        memory,
        profiles()["elderly"],
        now,
        music_on=flags.get("music_on", False),
        spoke_recently=flags.get("spoke_recently", False),
        last_line=flags.get("last_line", ""),
    )


def _ok(text: str) -> None:
    assert text.endswith((".", "?"))
    assert "_" not in text and "→" not in text and "/" not in text
    assert len(text.split()) <= 25


def test_one_face_touch_stays_quiet():
    clock = FrozenClock()
    scene = SceneState(
        people=[_person(actions=_acts("touch_face_eyes"))],
        objects_seen=[_sight("glasses", "on the side table")],
    )
    memory = ObjectMemory()
    memory.note_live(scene.objects_seen, clock.now())
    assert _guess(scene, memory, clock.now()) is None


def test_glasses_pattern_says_where_they_are():
    clock = FrozenClock()
    scene = SceneState(
        people=[_person(actions=_acts("touch_face_eyes", "pat_chest_pocket"), facing="camera")],
        objects_seen=[_sight("glasses", "on the side table", side="left")],
    )
    memory = ObjectMemory()
    memory.note_live(scene.objects_seen, clock.now())
    guess = _guess(scene, memory, clock.now())
    assert guess is not None
    assert guess.kind == "tell"
    assert "your right" in guess.text
    _ok(guess.text)


def test_a_few_minutes_ago_is_hedged():
    clock = FrozenClock()
    memory = ObjectMemory()
    memory.note_live([_sight("glasses", "on the side table", side="left")], clock.now())
    clock.advance(3 * 60)
    scene = SceneState(people=[_person(actions=_acts("touch_face_eyes", "pat_chest_pocket"), facing="away")])
    guess = _guess(scene, memory, clock.now())
    assert guess is not None
    assert "a few minutes ago" in guess.text
    assert "your left" in guess.text
    _ok(guess.text)


def test_stale_glasses_use_the_usual_spot():
    clock = FrozenClock()
    memory = ObjectMemory()
    memory.note_scan([_sight("glasses", "on the side table")], clock.now())
    clock.advance(20 * 60)
    scene = SceneState(people=[_person(actions=_acts("touch_face_eyes", "look_at_surfaces"))])
    guess = _guess(scene, memory, clock.now())
    assert guess is not None
    assert "usually" in guess.text
    _ok(guess.text)


def test_glasses_on_the_head():
    clock = FrozenClock()
    scene = SceneState(
        people=[_person(actions=_acts("touch_head_top", "look_at_surfaces"))],
        objects_seen=[_sight("glasses", "on their head", on_head=True)],
    )
    memory = ObjectMemory()
    memory.note_live(scene.objects_seen, clock.now())
    guess = _guess(scene, memory, clock.now())
    assert guess is not None
    assert "on top of your head" in guess.text


def test_wearing_glasses_is_not_a_search():
    clock = FrozenClock()
    scene = SceneState(
        people=[_person(wearing_glasses="yes", actions=_acts("touch_face_eyes", "pat_chest_pocket"))],
        objects_seen=[_sight("glasses", "on the side table")],
    )
    assert _guess(scene, ObjectMemory(), clock.now()) is None


def test_hold_chest_is_safety_and_cancels_the_search():
    clock = FrozenClock()
    scene = SceneState(
        people=[_person(actions=_acts("hold_chest", "touch_face_eyes", "pat_chest_pocket"))],
        objects_seen=[_sight("glasses", "on the side table")],
    )
    guess = _guess(scene, ObjectMemory(), clock.now())
    assert guess is not None
    assert guess.kind == "safety"


def test_speaking_or_two_people_or_a_hidden_face_stay_quiet():
    clock = FrozenClock()
    memory = ObjectMemory()
    glasses = [_sight("glasses", "on the side table")]
    memory.note_live(glasses, clock.now())
    speaking = SceneState(people=[_person(speaking="yes", actions=_acts("touch_face_eyes", "pat_chest_pocket"))], objects_seen=glasses)
    crowd = SceneState(
        people=[
            _person(actions=_acts("touch_face_eyes", "pat_chest_pocket")),
            _person(posture="sitting"),
        ],
        objects_seen=glasses,
    )
    hidden = SceneState(
        people=[_person(face_visible="no", actions=_acts("touch_face_eyes", "pat_chest_pocket"))],
        objects_seen=glasses,
    )
    assert _guess(speaking, memory, clock.now()) is None
    assert _guess(crowd, memory, clock.now()) is None
    assert _guess(hidden, memory, clock.now()) is None


def test_cup_ear_turns_music_up_once_and_respects_the_phone():
    clock = FrozenClock()
    book = _book()
    scene = SceneState(people=[_person(actions=[PersonAction(label="cup_ear", count=2)])])
    memory = ObjectMemory()
    first = _guess(scene, memory, clock.now(), book, music_on=True)
    assert first is not None and first.kind == "volume_up"
    book.note_done("volume_up", clock.now())
    assert _guess(scene, memory, clock.now(), book, music_on=True) is None
    later = clock.now() + timedelta(seconds=11)
    assert _guess(scene, memory, later, book, music_on=True) is not None
    phoning = SceneState(people=[_person(on_phone="yes", actions=[PersonAction(label="cup_ear", count=2)])])
    assert _guess(phoning, memory, later, _book(), music_on=True) is None


def test_cup_ear_without_music_repeats_only_a_recent_line():
    clock = FrozenClock()
    scene = SceneState(people=[_person(actions=[PersonAction(label="cup_ear", count=2)])])
    memory = ObjectMemory()
    assert _guess(scene, memory, clock.now()) is None
    again = _guess(scene, memory, clock.now(), spoke_recently=True, last_line="Your glasses are on the side table.")
    assert again is not None and again.kind == "repeat"
    assert again.text.startswith("Your glasses")


def test_a_still_palm_stops_music_and_a_wave_does_not():
    clock = FrozenClock()
    memory = ObjectMemory()
    stop = SceneState(people=[_person(gesture="stop")])
    guess = _guess(stop, memory, clock.now(), music_on=True)
    assert guess is not None and guess.kind == "stop_music"
    assert _guess(stop, memory, clock.now(), music_on=False) is None
    wave = SceneState(people=[_person(gesture="waving")])
    shield = SceneState(people=[_person(gesture="shielding_eyes")])
    assert _guess(wave, memory, clock.now(), music_on=True) is None
    assert _guess(shield, memory, clock.now(), music_on=True) is None


def test_water_names_the_cup_and_asks_when_none_is_tagged():
    clock = FrozenClock()
    memory = ObjectMemory()
    found = SceneState(
        people=[_person(actions=_acts("touch_throat", "cough"))],
        objects_seen=[_sight("cup", "on the kitchen counter", side="right")],
    )
    memory.note_live(found.objects_seen, clock.now())
    guess = _guess(found, memory, clock.now())
    assert guess is not None and guess.kind == "tell"
    assert "cup" in guess.text
    assert "right side" in guess.text
    assert "kitchen counter" in guess.text
    _ok(guess.text)
    missing = SceneState(people=[_person(actions=_acts("touch_throat", "cough"))])
    asked = _guess(missing, ObjectMemory(), clock.now())
    assert asked is not None and asked.kind == "ask"
    assert asked.text == "Would you like some water?"
    holding = SceneState(
        people=[_person(holding=["cup"], actions=_acts("touch_throat", "cough"))],
        objects_seen=[_sight("cup", "in their hand", with_person=True)],
    )
    assert _guess(holding, ObjectMemory(), clock.now()) is None
    once = SceneState(people=[_person(actions=_acts("cough"))])
    assert _guess(once, ObjectMemory(), clock.now()) is None


def test_clutching_the_throat_is_safety_not_a_drink():
    clock = FrozenClock()
    scene = SceneState(
        people=[_person(actions=_acts("clutch_throat", "cough"))],
        objects_seen=[_sight("cup", "on the kitchen counter")],
    )
    guess = _guess(scene, ObjectMemory(), clock.now())
    assert guess is not None
    assert guess.kind == "safety"
    assert guess.need == "choking"


def test_a_collar_adjustment_is_not_a_cough():
    clock = FrozenClock()
    scene = SceneState(people=[_person(actions=[])])
    assert _guess(scene, ObjectMemory(), clock.now()) is None


def test_i_am_fine_mutes_that_instinct():
    clock = FrozenClock()
    book = _book()
    scene = SceneState(
        people=[_person(actions=_acts("touch_face_eyes", "pat_chest_pocket"))],
        objects_seen=[_sight("glasses", "on the side table")],
    )
    memory = ObjectMemory()
    memory.note_live(scene.objects_seen, clock.now())
    assert _guess(scene, memory, clock.now(), book) is not None
    book.mute("glasses", clock.now())
    assert _guess(scene, memory, clock.now(), book) is None
    assert _guess(scene, memory, clock.now() + timedelta(minutes=11), book) is not None


def test_place_phrase_flips_left_when_they_face_the_camera():
    assert "your right" in place_phrase("on the side table", "left", "camera", "the window", "the door")
    assert "your left" in place_phrase("on the side table", "left", "away", "the window", "the door")
    anchored = place_phrase("on the side table", "left", "unclear", "the window", "the door")
    assert "near the window" in anchored
    assert place_phrase("on the side table", "middle", "camera", "the window", "the door") == "on the side table"
    assert place_phrase("on the side table", "unclear", "away", "the window", "the door") == "on the side table"


def test_speakable_rejects_labels_and_long_lines():
    fixed = speakable("touch_face_eyes → glasses / now")
    _ok(fixed)
    long = speakable(" ".join(["word"] * 40))
    assert len(long.split()) <= 25
    assert long.endswith(".")


def test_a_failed_double_check_stays_quiet():
    clock = FrozenClock()
    scene = SceneState(
        people=[_person(actions=_acts("touch_face_eyes", "pat_chest_pocket"))],
        objects_seen=[_sight("glasses", "on the side table", side="left")],
    )
    memory = ObjectMemory()
    memory.note_live(scene.objects_seen, clock.now())
    guess = _guess(scene, memory, clock.now())
    assert guess is not None
    assert confirm(guess, {"wearing": "no", "on_head": "no", "looking": "other", "where": "side table"}) is None
    agreed = confirm(guess, {"wearing": "no", "on_head": "no", "looking": "looking", "where": "side table left"})
    assert agreed is not None
    assert "check:" in agreed.chain
    head = confirm(guess, {"wearing": "no", "on_head": "yes", "looking": "looking", "where": "none"})
    assert head is not None and "on top of your head" in head.text


def test_cane_and_keys_at_the_door():
    clock = FrozenClock()
    memory = ObjectMemory()
    memory.note_live(
        [_sight("cane", "by the door", side="left"), _sight("keys", "on the kitchen counter")],
        clock.now(),
    )
    cane = SceneState(
        people=[_person(actions=_acts("reach_steady"), facing="unclear")],
        objects_seen=[_sight("cane", "by the door", side="left")],
    )
    found = _guess(cane, memory, clock.now())
    assert found is not None and found.kind == "tell"
    assert "cane" in found.text
    _ok(found.text)
    keys = SceneState(
        people=[_person(zone="door", actions=_acts("pat_pants_pockets", "look_at_surfaces"))],
        objects_seen=[_sight("keys", "on the kitchen counter", side="right")],
    )
    leaving = _guess(keys, memory, clock.now())
    assert leaving is not None
    assert "keys" in leaving.text
    _ok(leaving.text)
