import json
from pathlib import Path

import pytest

from fluffy.config import ConfigError, RoomConfig, default_config_path, load_home
from fluffy.export import export_schemas
from fluffy.tools.registry import ToolArgumentError, configure

ROOT = Path(__file__).resolve().parents[2]


def test_home_toml_loads_living_room_devices() -> None:
    home = load_home()
    assert home.path == default_config_path()
    room = home.rooms["living_room"]
    assert room.lights == {
        "lamp.living_floor": "floor lamp next to the sofa",
        "light.living_ceiling": "ceiling light",
    }
    assert room.blinds == ("blinds.living_window",)
    assert room.speaker == "speaker.living"
    assert room.display == "companion.living"
    assert home.sounds == ("rain", "chime", "white_noise", "doorbell")
    assert home.colors == ("blue", "red", "amber", "white")
    assert home.patterns == ("pulse1", "pulse3", "strobe")
    assert home.caregiver_name == "Alex"


def test_invalid_config_reports_duplicate_devices(tmp_path: Path) -> None:
    path = tmp_path / "home.toml"
    path.write_text(
        """
[room.living_room]
lights = { "lamp.shared" = "floor lamp" }
blinds = ["lamp.shared"]
speaker = "speaker.living"
display = "companion.living"

[sounds]
names = ["rain"]

[alerts]
colors = ["blue"]
patterns = ["pulse1"]

[caregiver]
name = "Alex"
""".strip(),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="lamp.shared"):
        load_home(path)


def test_tool_enums_come_from_home_toml() -> None:
    home = load_home()
    manifest = configure(home).manifest()
    by_name = {tool["name"]: tool for tool in manifest["tools"]}
    assert by_name["dim_lights"]["parameters"]["properties"]["room"]["enum"] == ["living_room"]
    assert by_name["dim_lights"]["parameters"]["properties"]["light"]["anyOf"][0]["enum"] == [
        "lamp.living_floor",
        "light.living_ceiling",
    ]
    assert by_name["play_sound"]["parameters"]["properties"]["sound"]["enum"] == list(home.sounds)
    assert by_name["flash_lights"]["parameters"]["properties"]["color"]["enum"] == list(home.colors)
    assert by_name["flash_lights"]["parameters"]["properties"]["pattern"]["enum"] == list(home.patterns)
    assert by_name["dim_lights"]["action_class"] == "comfort"
    assert by_name["flash_lights"]["action_class"] == "notify"
    assert by_name["show_caption"]["action_class"] == "notify"
    assert by_name["alert_caregiver"]["action_class"] == "safety"
    assert "confidence" not in by_name["dim_lights"]["parameters"]["properties"]
    assert by_name["dim_lights"]["model_parameters"]["properties"]["confidence"]["minimum"] == 0
    assert "confidence" in by_name["dim_lights"]["model_parameters"]["required"]
    assert "reason" in by_name["dim_lights"]["model_parameters"]["required"]


def test_added_sound_appears_in_the_schema() -> None:
    home = load_home()
    widened = type(home)(
        rooms=home.rooms,
        sounds=(*home.sounds, "birds"),
        colors=home.colors,
        patterns=home.patterns,
        caregiver_name=home.caregiver_name,
        path=home.path,
    )
    schema = configure(widened).execution_schema("play_sound")
    assert schema["properties"]["sound"]["enum"][-1] == "birds"


def test_model_call_strips_confidence_and_rejects_bad_arguments() -> None:
    bound = configure(load_home())
    parsed = bound.parse_model_call(
        "dim_lights",
        json.dumps(
            {
                "room": "living_room",
                "luminosity": 35,
                "light": "lamp.living_floor",
                "confidence": 0.88,
                "reason": "the person asked",
            }
        ),
    )
    assert parsed.args == {"room": "living_room", "luminosity": 35, "light": "lamp.living_floor"}
    assert parsed.confidence == 0.88
    assert parsed.reason == "the person asked"
    assert parsed.action_class == "comfort"

    with pytest.raises(ToolArgumentError, match="malformed"):
        bound.parse_model_call("dim_lights", "{")
    with pytest.raises(ToolArgumentError, match="luminosity"):
        bound.parse_model_call(
            "dim_lights",
            {"room": "living_room", "luminosity": 101, "confidence": 0.2, "reason": "too high"},
        )
    with pytest.raises(ToolArgumentError, match="unknown room"):
        bound.parse_model_call(
            "dim_lights",
            {"room": "kitchen", "luminosity": 10, "confidence": 0.2, "reason": "no such room"},
        )
    with pytest.raises(ToolArgumentError, match="confidence and reason"):
        bound.parse_model_call("dim_lights", {"room": "living_room", "luminosity": 10})
    with pytest.raises(ToolArgumentError, match="removed before the plan"):
        bound.parse_plan_arguments(
            "dim_lights",
            {"room": "living_room", "luminosity": 10, "confidence": 1, "reason": "later"},
        )
    with pytest.raises(ToolArgumentError, match="Extra"):
        bound.parse_plan_arguments(
            "dim_lights",
            {"room": "living_room", "luminosity": 10, "surprise": True},
        )
    with pytest.raises(ToolArgumentError):
        bound.parse_plan_arguments(
            "flash_lights",
            {"room": "living_room", "color": "blue", "pattern": "pulse3", "duration_s": 11},
        )
    with pytest.raises(ToolArgumentError):
        bound.parse_plan_arguments(
            "show_caption",
            {"text": "x" * 141, "size": "xl", "priority": "high"},
        )
    with pytest.raises(ToolArgumentError, match="unknown tool"):
        bound.parse_plan_arguments("unlock_door", {})


def test_light_must_belong_to_its_room() -> None:
    base = load_home()
    rooms = dict(base.rooms)
    rooms["porch"] = RoomConfig(
        name="porch",
        lights={"lamp.porch": "porch lamp"},
        blinds=(),
        speaker="speaker.porch",
        display="companion.porch",
    )
    home = type(base)(
        rooms=rooms,
        sounds=base.sounds,
        colors=base.colors,
        patterns=base.patterns,
        caregiver_name=base.caregiver_name,
        path=base.path,
    )
    bound = configure(home)
    with pytest.raises(ToolArgumentError, match="not in room"):
        bound.parse_plan_arguments(
            "dim_lights",
            {"room": "porch", "luminosity": 10, "light": "lamp.living_floor"},
        )


def test_committed_schemas_match_export(tmp_path: Path) -> None:
    written = export_schemas(tmp_path, load_home())
    for path in written:
        committed = ROOT / "schemas" / path.name
        assert committed.read_text(encoding="utf-8") == path.read_text(encoding="utf-8")
