from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from fluffy.clock import FrozenClock
from fluffy.config import load_home
from fluffy.tools.home_state import CAPTION_TTL, HomeState
from fluffy.tools.registry import configure
from mockhome.app import create_app


def _bound_state(clock: FrozenClock) -> tuple[HomeState, object]:
    home = load_home()
    bound = configure(home)
    return HomeState.from_config(home, now=clock.now()), bound


def test_flash_restores_previous_light_state() -> None:
    clock = FrozenClock()
    state, bound = _bound_state(clock)
    state.last_source = "manual"
    dim = bound.parse_plan_arguments(
        "dim_lights",
        {"room": "living_room", "luminosity": 40, "light": "lamp.living_floor"},
    )
    bound.apply(state, "dim_lights", dim, clock.now())
    warmth = bound.parse_plan_arguments(
        "set_light_warmth",
        {"room": "living_room", "warmth": "warm"},
    )
    bound.apply(state, "set_light_warmth", warmth, clock.now())
    flash = bound.parse_plan_arguments(
        "flash_lights",
        {"room": "living_room", "color": "blue", "pattern": "pulse3", "duration_s": 2},
    )
    effect = bound.apply(state, "flash_lights", flash, clock.now())
    assert effect.new_state["flash"]["color"] == "blue"
    assert state.rooms["living_room"].lights["lamp.living_floor"].color == "blue"
    during = bound.parse_plan_arguments(
        "dim_lights",
        {"room": "living_room", "luminosity": 10, "light": "lamp.living_floor"},
    )
    bound.apply(state, "dim_lights", during, clock.now())
    clock.advance(2)
    restored = state.expire(clock.now())
    assert restored
    lamp = state.rooms["living_room"].lights["lamp.living_floor"]
    ceiling = state.rooms["living_room"].lights["light.living_ceiling"]
    assert lamp.luminosity == 10
    assert lamp.warmth == "warm"
    assert lamp.color is None
    assert ceiling.luminosity == 100
    assert ceiling.warmth == "warm"
    assert ceiling.color is None
    assert state.rooms["living_room"].flash is None
    assert state.snapshot()["rooms"]["living_room"]["flash"] is None


def test_caption_expires_unless_priority_is_high() -> None:
    clock = FrozenClock()
    state, bound = _bound_state(clock)
    state.last_source = "manual"
    normal = bound.parse_plan_arguments(
        "show_caption",
        {"text": "Doorbell. Someone is at the front door.", "size": "xl", "priority": "normal"},
    )
    bound.apply(state, "show_caption", normal, clock.now())
    assert state.rooms["living_room"].caption is not None
    clock.advance(CAPTION_TTL.total_seconds())
    state.expire(clock.now())
    assert state.rooms["living_room"].caption is None

    sticky = bound.parse_plan_arguments(
        "show_caption",
        {"text": "Are you okay?", "size": "xl", "priority": "high"},
    )
    bound.apply(state, "show_caption", sticky, clock.now())
    clock.advance(60)
    state.expire(clock.now())
    assert state.rooms["living_room"].caption is not None
    assert state.rooms["living_room"].caption.text == "Are you okay?"
    state.dismiss_caption(clock.now(), "manual")
    assert state.rooms["living_room"].caption is None


def test_caregiver_alert_can_be_acknowledged() -> None:
    clock = FrozenClock()
    state, bound = _bound_state(clock)
    state.last_source = "executor:pln_safety0001"
    args = bound.parse_plan_arguments(
        "alert_caregiver",
        {"severity": "high", "message": "Sam may have fallen in the living room", "include_snapshot": False},
    )
    effect = bound.apply(state, "alert_caregiver", args, clock.now())
    alert = effect.new_state["alert"]
    assert alert["snapshot"] is None
    assert alert["include_snapshot"] is False
    assert alert["acknowledged"] is False
    assert alert["source"] == "executor:pln_safety0001"
    acked = state.acknowledge(alert["alert_id"], clock.now() + timedelta(seconds=1), "manual")
    assert acked.new_state["alert"]["acknowledged"] is True
    assert acked.new_state["alert"]["acknowledged_at"] is not None
    again = state.acknowledge(alert["alert_id"], clock.now() + timedelta(seconds=2), "manual")
    assert again.new_state["alert"]["acknowledged_at"] == acked.new_state["alert"]["acknowledged_at"]


def test_http_tool_call_records_source_and_doorbell() -> None:
    clock = FrozenClock(datetime(2026, 10, 18, 22, 41, 5, tzinfo=timezone.utc))
    with TestClient(create_app(load_home(), clock)) as client:
        denied = client.post("/api/tools/dim_lights", json={"room": "attic", "luminosity": 10})
        assert denied.status_code == 422
        missing = client.post("/api/tools/unlock_door", json={})
        assert missing.status_code == 404
        applied = client.post(
            "/api/tools/dim_lights",
            json={"room": "living_room", "luminosity": 30, "light": "lamp.living_floor"},
            headers={"X-Plan-Id": "pln_http000001"},
        )
        assert applied.status_code == 200
        body = applied.json()
        assert body["source"] == "executor:pln_http000001"
        assert body["previous_state"]["lights"]["lamp.living_floor"]["luminosity"] == 100
        assert body["new_state"]["lights"]["lamp.living_floor"]["luminosity"] == 30
        snapshot = client.get("/api/state").json()
        assert snapshot["rooms"]["living_room"]["lights"]["lamp.living_floor"]["luminosity"] == 30
        assert snapshot["last_source"] == "executor:pln_http000001"
        with client.websocket_connect("/ws/state") as socket:
            initial = socket.receive_json()
            assert initial["type"] == "state.snapshot"
            doorbell = client.post("/api/sensors/doorbell")
            assert doorbell.status_code == 200
            assert doorbell.json()["event"] == "doorbell"
            event = socket.receive_json()
            assert event["type"] == "sensor.event"
            assert event["event"] == "doorbell"
            assert event["room"] == "living_room"


def test_initial_room_matches_the_config() -> None:
    clock = FrozenClock()
    state, _bound = _bound_state(clock)
    room = state.snapshot()["rooms"]["living_room"]
    assert room["lights"]["lamp.living_floor"]["description"] == "floor lamp next to the sofa"
    assert room["blinds"]["blinds.living_window"]["position"] == 100
    assert room["speaker"] == {"device": "speaker.living", "sound": None, "volume": 0}
    assert room["display"] == "companion.living"
    assert state.snapshot()["caregiver"]["name"] == "Alex"
