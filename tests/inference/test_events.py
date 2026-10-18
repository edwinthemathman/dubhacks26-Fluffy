from fluffy.clock import FrozenClock
from fluffy.perception.scene import PersonState, SceneState
from fluffy.perception.scene import ObjectSighting
from fluffy.world.model import WorldModel


def _person(**kwargs) -> PersonState:
    return PersonState(**kwargs)


def test_fall_fires_after_the_dwell_and_only_once():
    clock = FrozenClock()
    world = WorldModel(clock)
    scene = SceneState(
        confidence=0.9,
        people=[_person(on_floor=True, location_surface="floor", posture="lying", eyes="closed", motion="none")],
    )
    assert world.observe_scene(scene) == []
    clock.advance(19)
    assert world.observe_scene(scene) == []
    clock.advance(1)
    events = world.observe_scene(scene)
    assert [event.kind for event in events] == ["possible_fall"]
    assert events[0].handled_as == "safety"
    clock.advance(5)
    assert world.observe_scene(scene) == []


def test_sofa_and_bed_are_not_falls():
    clock = FrozenClock()
    world = WorldModel(clock)
    for surface in ("sofa", "bed"):
        scene = SceneState(people=[_person(on_floor=True, location_surface=surface)])
        world.observe_scene(scene)
        clock.advance(30)
        kinds = [event.kind for event in world.observe_scene(scene)]
        assert "possible_fall" not in kinds


def test_distress_phrases_and_the_sos_button():
    clock = FrozenClock()
    world = WorldModel(clock)
    assert world.observe_transcript("I fell")[0].kind == "distress_call"
    assert world.observe_transcript("I can't get up")[0].handled_as == "safety"
    assert world.observe_transcript("help") == []
    assert world.observe_transcript("Fluffy, can you help me get a bottle of water?") == []
    assert world.observe_transcript("Fluffy, help me find my keys") == []
    assert world.observe_sos()[0].detail["source"] == "sos_button"


def test_sound_labels_notify_immediately():
    clock = FrozenClock()
    world = WorldModel(clock)
    event = world.observe_sound("doorbell")[0]
    assert event.kind == "doorbell"
    assert event.handled_as == "notify"
    assert world.observe_sound("none") == []
    assert world.observe_sound("smoke_alarm")[0].kind == "smoke_alarm"


def test_glare_and_darkness_are_offers_after_dwell():
    clock = FrozenClock()
    world = WorldModel(clock)
    glare = SceneState(people=[_person(gesture="shielding_eyes")])
    world.observe_scene(glare)
    clock.advance(10)
    events = world.observe_scene(glare)
    assert events[0].kind == "glare_signal"
    assert events[0].handled_as == "offer"

    clock = FrozenClock()
    world = WorldModel(clock)
    dim = SceneState(lighting="dim")
    world.observe_scene(dim)
    clock.advance(15)
    assert world.observe_scene(dim) == []

    dark = SceneState(lighting="dark")
    world.observe_scene(dark)
    clock.advance(10)
    events = world.observe_scene(dark)
    assert events[0].kind == "getting_dark"
    assert events[0].handled_as == "offer"


def test_pointing_is_context_only_while_someone_is_speaking():
    clock = FrozenClock()
    world = WorldModel(clock)
    scene = SceneState(
        people=[_person(gesture="pointing", attention_target="lamp.living_floor")]
    )
    assert world.observe_scene(scene) == []
    world.speech_active = True
    events = world.observe_scene(scene)
    assert events[0].kind == "pointing_at_device"
    assert events[0].handled_as == "context"
    assert events[0].detail["attention_target"] == "lamp.living_floor"


def test_snapshot_includes_profile_speech_and_objects():
    clock = FrozenClock()
    world = WorldModel(clock)
    world.observe_scene(
        SceneState(
            summary="One person on the sofa.",
            objects_seen=[ObjectSighting(name="keys", location_desc="on the table")],
        )
    )
    world.observe_transcript("where are my keys")
    world.mirror_devices({"lamp.living_floor": "100"})
    first = world.snapshot(profile_summary="blind", open_questions=["Dim the floor lamp?"])
    second = world.snapshot(profile_summary="blind", open_questions=["Dim the floor lamp?"])
    assert first.id == second.id
    assert "blind" in first.text
    assert "where are my keys" in first.text
    assert "on the table" in first.text
    assert "Dim the floor lamp?" in first.text
    assert first.approx_tokens <= 600

    world.observe_transcript("and my phone")
    changed = world.snapshot(profile_summary="blind", open_questions=[])
    assert changed.id != first.id
