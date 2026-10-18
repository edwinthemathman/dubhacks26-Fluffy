"""Room presence: startup is quiet, and a fall is not someone leaving."""

from fluffy.live.presence import RoomPresence
from fluffy.perception.scene import PersonState, SceneState


def _person(**overrides) -> SceneState:
    fields = dict(posture="standing", activity="idle")
    fields.update(overrides)
    return SceneState(people=[PersonState(**fields)])  # type: ignore[arg-type]


def _empty(*sounds: str) -> SceneState:
    return SceneState(people=[], sounds=list(sounds or ["none"]))  # type: ignore[arg-type]


def test_startup_fires_nothing():
    room = RoomPresence()
    assert room.observe(_person(), 0, "Sam").cues == set()
    assert room.observe(_empty(), 0, "Sam").cues == set()


def test_empty_then_a_person_fires_entered_room_once():
    room = RoomPresence()
    assert room.observe(_empty(), 0, "Sam").cues == set()
    assert room.observe(_empty(), 6, "Sam").cues == set()
    arrived = room.observe(_person(posture="walking"), 12, "Sam")
    assert arrived.cues == {"entered_room"}
    assert arrived.room_change == "Sam just walked in; the room was empty for 12 s"
    assert room.observe(_person(), 13, "Sam").cues == set()


def test_standing_then_gone_fires_left_room_once():
    room = RoomPresence()
    room.observe(_person(posture="standing"), 0, "Sam")
    assert room.observe(_empty(), 1, "Sam").cues == set()
    left = room.observe(_empty(), 6, "Sam")
    assert left.cues == {"left_room"}
    assert left.room_change == "Sam just left the room"
    assert room.observe(_empty(), 12, "Sam").cues == set()


def test_last_seen_on_the_floor_is_not_leaving():
    room = RoomPresence()
    room.observe(_person(posture="lying", on_floor=True, location_surface="floor", fell=True), 0, "Sam")
    room.observe(_empty(), 1, "Sam")
    assert room.observe(_empty(), 8, "Sam").cues == set()


def test_a_one_read_flicker_fires_nothing():
    room = RoomPresence()
    room.observe(_person(), 0, "Sam")
    assert room.observe(_empty(), 3, "Sam").cues == set()
    assert room.observe(_person(), 4, "Sam").cues == set()
