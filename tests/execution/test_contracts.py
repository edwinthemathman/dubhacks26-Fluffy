"""Schema 2 contracts, registry validation, and evidence policy."""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from fluffy.config import load_home_config, repo_root
from fluffy.evidence.evaluator import ContextView, evaluate_action
from fluffy.models.contracts import PlanProposed, SchemaVersionError, parse_schema_version
from fluffy.tools.registry import ToolError, build_registry

NOW = datetime(2026, 10, 18, 1, 0, tzinfo=timezone.utc)


@pytest.fixture()
def home():
    return load_home_config(repo_root() / "config" / "home.toml", {})


@pytest.fixture()
def registry(home):
    return build_registry(home)


def test_registry_rejects_bad_arguments(registry):
    with pytest.raises(ToolError) as unknown:
        registry.validate_args("make_coffee", {"room": "living_room"})
    assert unknown.value.error_code == "unknown_tool"

    with pytest.raises(ToolError):
        registry.validate_args("dim_lights", {"room": "living_room", "luminosity": 140})

    with pytest.raises(ToolError):
        registry.validate_args("dim_lights", {"room": "living_room", "luminosity": 40, "confidence": 0.99})

    with pytest.raises(ToolError):
        registry.validate_args("dim_lights", {"room": "attic", "luminosity": 40})

    with pytest.raises(ToolError):
        registry.validate_args("dim_lights", {"room": "living_room", "luminosity": True})

    with pytest.raises(ToolError):
        registry.validate_args("dim_lights", {"room": "living_room", "luminosity": 40.5})

    model = registry.validate_args(
        "dim_lights",
        {"room": "living_room", "luminosity": 35, "light": "lamp.living_floor"},
    )
    assert model.luminosity == 35


def test_schema_version_rejects_v1():
    with pytest.raises(SchemaVersionError):
        parse_schema_version({"schema_version": "1.0"})
    with pytest.raises(ValidationError):
        PlanProposed.model_validate({"type": "plan.proposed", "schema_version": "1.0", "plan": {}})


def test_manifest_has_no_confidence_field(registry):
    manifest = registry.manifest({name: True for name in registry.names()})
    assert manifest["schema_version"] == "2.0"
    dim = next(tool for tool in manifest["tools"] if tool["name"] == "dim_lights")
    assert "confidence" not in dim["parameters"].get("properties", {})
    assert "living_room" in dim["parameters"]["properties"]["room"]["enum"]


def _obs(home, **overrides):
    from fluffy.models.contracts import Observation

    base = dict(
        observation_id="obs_audio_17",
        session_id="ses_demo",
        kind="audio",
        captured_at=NOW,
        room="living_room",
        supports=["clap"],
        targets=["living_room"],
        summary="A clap was heard.",
        source="fixture",
    )
    base.update(overrides)
    return Observation(**base)


def test_evidence_clap_needs_corroboration(home):
    context = ContextView(now=NOW)
    audio = _obs(home)
    video = _obs(
        home,
        observation_id="obs_video_18",
        kind="video",
        captured_at=NOW - timedelta(seconds=1),
        supports=["clap"],
        targets=["living_room"],
        summary="Hands met.",
    )
    decision = evaluate_action(
        home,
        "play_sound",
        {"room": "living_room", "sound": "arrival_song", "volume": 35},
        [audio, video],
        context,
        "clap",
        explicit=False,
    )
    assert decision.score == 5
    assert decision.outcome == "automatic"

    weak = evaluate_action(
        home,
        "play_sound",
        {"room": "living_room", "sound": "arrival_song", "volume": 35},
        [audio],
        context,
        "clap",
        explicit=False,
    )
    assert weak.score < 5
    assert weak.outcome in {"ask", "quiet"}


def test_stale_evidence_is_not_automatic(home):
    stale = _obs(home, captured_at=NOW - timedelta(seconds=30))
    decision = evaluate_action(
        home,
        "dim_lights",
        {"room": "living_room", "luminosity": 20, "light": "lamp.living_floor"},
        [stale],
        ContextView(now=NOW),
        "glare",
        explicit=False,
    )
    assert decision.checks[2].name == "evidence_fresh"
    assert decision.checks[2].passed is False
    assert decision.outcome != "automatic"


def test_explicit_request_scores_when_grounded(home):
    spoken = _obs(
        home,
        kind="speech",
        supports=["explicit_request"],
        targets=["living_room", "lamp.living_floor"],
        summary="Please dim the floor lamp.",
    )
    decision = evaluate_action(
        home,
        "dim_lights",
        {"room": "living_room", "luminosity": 30, "light": "lamp.living_floor"},
        [spoken],
        ContextView(now=NOW),
        "explicit_request",
        explicit=True,
    )
    assert decision.score == 5
    assert decision.outcome == "automatic"
