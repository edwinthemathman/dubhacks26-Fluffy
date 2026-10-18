"""Object tags: searching, left behind, dropped, out of reach, and retagging."""

from fluffy.brain.objects import find_line, object_notices, wants_room_scan
from fluffy.clock import FrozenClock
from fluffy.live.loop import LiveLoop
from fluffy.perception.scene import ObjectSighting, PersonState, SceneState
from fluffy.world.model import ObjectMemory, objects_at_end
from tests.inference.helpers import profiles


def _sight(name, place, **flags):
    return ObjectSighting(name=name, location_desc=place, **flags)


def _scene(*people, objects=None, timeline=None):
    return SceneState(people=list(people), objects_seen=objects or [], timeline=timeline or [])


def _memory(clock, *sightings):
    memory = ObjectMemory()
    memory.note_live(list(sightings), clock.now())
    return memory


def test_searching_asks_with_tagged_choices():
    clock = FrozenClock()
    scene = _scene(
        PersonState(searching=True, posture="standing"),
        objects=[_sight("wallet", "on the side table"), _sight("keys", "by the door")],
    )
    notices = object_notices(scene, _memory(clock, *_scene_objects(scene)), profiles()["blind"], clock.now())
    assert len(notices) == 1
    assert notices[0].kind == "searching"
    assert notices[0].text == "Which are you looking for?"
    assert "wallet" in notices[0].choices
    assert "keys" in notices[0].choices
    assert "Something else" in notices[0].choices


def test_looking_for_skips_the_question():
    clock = FrozenClock()
    scene = _scene(
        PersonState(searching=True, looking_for="keys", posture="standing"),
        objects=[_sight("keys", "on the side table")],
    )
    notices = object_notices(scene, _memory(clock, *_scene_objects(scene)), profiles()["blind"], clock.now())
    assert notices[0].kind == "where"
    assert notices[0].text == "Your keys are on the side table."
    assert notices[0].choices == ()


def test_leaving_without_a_wallet_notices_it():
    clock = FrozenClock()
    scene = _scene(
        PersonState(activity="leaving", posture="walking"),
        objects=[_sight("wallet", "on the side table")],
    )
    notices = object_notices(scene, _memory(clock, *_scene_objects(scene)), profiles()["blind"], clock.now())
    assert any(notice.kind == "left_behind" and "wallet" in notice.text for notice in notices)


def test_carrying_the_wallet_is_quiet():
    clock = FrozenClock()
    scene = _scene(
        PersonState(activity="leaving", posture="walking"),
        objects=[_sight("wallet", "in their hand", with_person=True)],
    )
    notices = object_notices(scene, _memory(clock, *_scene_objects(scene)), profiles()["blind"], clock.now())
    assert notices == []


def test_cane_on_the_floor_is_dropped():
    clock = FrozenClock()
    scene = _scene(
        PersonState(posture="standing"),
        objects=[_sight("cane", "on the floor by the sofa", on_floor=True)],
    )
    notices = object_notices(
        scene,
        _memory(clock, *_scene_objects(scene)),
        profiles()["blind"],
        clock.now(),
        previous_floor={"cane": False},
    )
    assert notices[0].kind == "dropped"
    assert notices[0].text == "Your cane fell, it's on the floor by the sofa."


def test_sitting_with_the_cane_across_the_room_offers_help():
    clock = FrozenClock()
    scene = _scene(
        PersonState(posture="sitting"),
        objects=[_sight("cane", "by the door")],
    )
    notices = object_notices(scene, _memory(clock, *_scene_objects(scene)), profiles()["blind"], clock.now())
    assert notices[0].kind == "out_of_reach"
    assert notices[0].text == "Your cane is by the door. Want me to tell Alex?"


def test_the_same_notice_waits_two_minutes():
    clock = FrozenClock()
    scene = _scene(PersonState(posture="sitting"), objects=[_sight("cane", "by the door")])
    memory = _memory(clock, *_scene_objects(scene))
    profile = profiles()["blind"]
    fired: dict = {}
    assert object_notices(scene, memory, profile, clock.now(), fired=fired)
    assert object_notices(scene, memory, profile, clock.now(), fired=fired) == []
    clock.advance(2 * 60)
    assert object_notices(scene, memory, profile, clock.now(), fired=fired)


def test_a_fall_suppresses_object_notices():
    clock = FrozenClock()
    scene = _scene(
        PersonState(fell=True, posture="lying", on_floor=True, activity="fallen"),
        objects=[_sight("wallet", "on the side table")],
        timeline=["walks out"],
    )
    notices = object_notices(
        scene,
        _memory(clock, *_scene_objects(scene)),
        profiles()["blind"],
        clock.now(),
        departing=True,
    )
    assert notices == []


def test_find_object_answers_from_memory():
    clock = FrozenClock()
    memory = _memory(clock, _sight("wallet", "on the side table by the sofa"))
    line = find_line(memory.recall("wallet", clock.now()), clock.now())
    assert line == "wallet: on the side table by the sofa, seen 0 min ago, not with them"


def test_retagging_keeps_a_missed_object_and_falls_back_to_home():
    clock = FrozenClock()
    memory = ObjectMemory()
    memory.note_scan([_sight("wallet", "on the side table")], clock.now())
    recall = memory.recall("wallet", clock.now())
    assert recall.home_desc == "on the side table"
    assert recall.location_desc == "on the side table"

    memory.note_live([], clock.now())
    assert not memory.recall("wallet", clock.now()).unconfirmed
    memory.note_live([], clock.now())
    missed = memory.recall("wallet", clock.now())
    assert missed.unconfirmed
    assert missed.location_desc == "on the side table"

    memory.note_live([_sight("wallet", "by the door")], clock.now())
    moved = memory.recall("wallet", clock.now())
    assert moved.location_desc == "by the door"
    assert not moved.unconfirmed

    clock.advance(16 * 60)
    stale = find_line(memory.recall("wallet", clock.now()), clock.now())
    assert stale == "wallet: usually on the side table"


def test_setting_an_object_down_replaces_where_it_was():
    scene = _scene(
        objects=[_sight("phone", "in their hand", with_person=True)],
        timeline=["holds the phone", "puts the phone on the table"],
    )
    ended = objects_at_end(scene)
    assert len(ended) == 1
    assert ended[0].location_desc == "on the table"
    assert ended[0].with_person is False

    clock = FrozenClock()
    memory = ObjectMemory()
    memory.note_live([_sight("phone", "in their hand", with_person=True)], clock.now())
    memory.note_live(objects_at_end(scene), clock.now())
    recall = memory.recall("phone", clock.now())
    assert recall.location_desc == "on the table"
    assert recall.with_person is False


def test_a_later_scan_moves_the_tag_to_where_it_is_now():
    clock = FrozenClock()
    memory = ObjectMemory()
    memory.note_live([_sight("phone", "in their hand", with_person=True)], clock.now())
    memory.note_scan([_sight("phone", "on the table")], clock.now())
    recall = memory.recall("phone", clock.now())
    assert recall.location_desc == "on the table"
    assert recall.home_desc == "on the table"
    assert recall.with_person is False


def test_puts_it_down_still_means_the_phone_they_were_holding():
    scene = _scene(
        objects=[_sight("phone", "in their hand", with_person=True)],
        timeline=["puts it on the table"],
    )
    ended = objects_at_end(scene)
    assert ended[0].location_desc == "on the table"
    assert ended[0].with_person is False


def test_picking_it_back_up_after_setting_it_down_tags_the_hand():
    scene = _scene(
        objects=[_sight("phone", "on the table")],
        timeline=["puts the phone on the table", "picks up the phone"],
    )
    ended = objects_at_end(scene)
    assert ended[0].location_desc == "in their hand"
    assert ended[0].with_person is True


def test_room_scan_triggers():
    assert wants_room_scan(empty_for_s=61, entered=False, said="", already_scanned_empty=False) == "empty"
    assert wants_room_scan(empty_for_s=90, entered=False, said="", already_scanned_empty=True) is None
    assert wants_room_scan(empty_for_s=0, entered=True, said="", already_scanned_empty=False) == "entered_room"
    assert (
        wants_room_scan(empty_for_s=0, entered=False, said="Fluffy, scan the room", already_scanned_empty=False)
        == "scan the room"
    )


def test_the_loop_asks_for_a_scan_on_those_same_cues():
    loop = LiveLoop(object(), profiles(), clock=FrozenClock())
    empty = SceneState()
    loop.presence.empty_first_at = loop.now() - 61
    loop._consider_scan(empty, set())
    assert loop.want_scan and loop.scanned_empty

    loop.want_scan = False
    loop._consider_scan(empty, set())
    assert not loop.want_scan

    loop._consider_scan(SceneState(people=[PersonState()]), {"entered_room"})
    assert loop.want_scan
    assert loop.texted_left == set()


def _scene_objects(scene: SceneState):
    return scene.objects_seen
