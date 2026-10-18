from fluffy.clock import FrozenClock
from fluffy.perception.scene import ObjectSighting, SceneState
from fluffy.world.model import WorldModel


def test_fresh_sighting_is_recalled_without_a_live_look():
    clock = FrozenClock()
    world = WorldModel(clock)
    world.observe_scene(
        SceneState(objects_seen=[ObjectSighting(name="keys", location_desc="on the table")])
    )
    recall = world.recall_object("keys")
    assert recall.status == "fresh"
    assert recall.location_desc == "on the table"


def test_sighting_goes_stale_after_fifteen_minutes():
    clock = FrozenClock()
    world = WorldModel(clock)
    world.observe_scene(
        SceneState(objects_seen=[ObjectSighting(name="glasses", location_desc="by the lamp")])
    )
    clock.advance(15 * 60)
    assert world.recall_object("glasses").status == "fresh"
    clock.advance(1)
    recall = world.recall_object("glasses")
    assert recall.status == "stale"
    assert recall.location_desc == "by the lamp"


def test_unknown_object_needs_a_live_look():
    world = WorldModel(FrozenClock())
    recall = world.recall_object("passport")
    assert recall.status == "unknown"
    assert recall.location_desc is None
