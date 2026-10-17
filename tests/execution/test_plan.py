from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from fluffy.config import load_home
from fluffy.models.plan import (
    PLAN_TTL,
    Action,
    Consent,
    Evidence,
    Plan,
    PlanDecision,
    PlanProposed,
    PlanValidationError,
    Trigger,
    normalize_plan,
    wire_schema,
)
from fluffy.models.scene import SceneState, scene_schema
from fluffy.tools.registry import configure


def _plan(**updates: object) -> PlanProposed:
    now = datetime(2026, 10, 18, 22, 41, 5, tzinfo=timezone.utc)
    payload = {
        "plan_id": "pln_demo000001",
        "created_at": now,
        "expires_at": now + PLAN_TTL,
        "trigger": Trigger(kind="inferred", utterance="that one dimmer", event=None, room="living_room"),
        "action_class": "comfort",
        "intent": "Dim the floor lamp the user pointed at",
        "actions": [
            Action(
                action_id="act_1",
                tool="dim_lights",
                args={"room": "living_room", "luminosity": 35, "light": "lamp.living_floor"},
            )
        ],
        "confidence": 0.88,
        "consent": Consent(
            required=True,
            given_by="nod",
            at=now,
            question="Dim the floor lamp?",
        ),
        "reasoning_summary": "The person pointed at the floor lamp and asked for it to be dimmer.",
        "evidence": [Evidence(kind="speech", text="that one dimmer")],
    }
    payload.update(updates)
    return PlanProposed(plan=Plan(**payload))  # type: ignore[arg-type]


def test_plan_round_trip_matches_exported_schema() -> None:
    bound = configure(load_home())
    message = normalize_plan(_plan(), bound)
    raw = message.model_dump(mode="json")
    again = PlanProposed.model_validate(raw)
    assert again == message
    assert raw["type"] == "plan.proposed"
    assert raw["schema_version"] == "1.0"
    assert raw["plan"]["evidence"][0]["text"] == "that one dimmer"
    schema = wire_schema()
    assert schema["title"] == "FluffyExecutorLink"
    assert "PlanProposed" in schema["$defs"]
    assert message.plan.expires_at - message.plan.created_at == timedelta(seconds=30)


def test_normalize_rejects_mislabeled_comfort_plan() -> None:
    bound = configure(load_home())
    comfort = _plan()
    mislabeled = comfort.model_copy(
        update={"plan": comfort.plan.model_copy(update={"action_class": "notify", "consent": Consent(required=False)})}
    )
    with pytest.raises(PlanValidationError, match="action_class must be comfort"):
        normalize_plan(mislabeled, bound)


def test_normalize_caps_expiry_at_thirty_seconds() -> None:
    bound = configure(load_home())
    now = datetime(2026, 10, 18, 22, 41, 5, tzinfo=timezone.utc)
    proposed = _plan()
    long_lived = proposed.model_copy(
        update={"plan": proposed.plan.model_copy(update={"expires_at": now + timedelta(minutes=5)})}
    )
    normalized = normalize_plan(long_lived, bound)
    assert normalized.plan.expires_at == now + timedelta(seconds=30)


def test_plan_rejects_unknown_consent_and_trigger() -> None:
    with pytest.raises(ValidationError):
        Consent(required=True, given_by="shrug", at=datetime.now(timezone.utc))  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Trigger(kind="guessed")  # type: ignore[arg-type]


def test_decision_and_scene_contracts() -> None:
    decision = PlanDecision(plan_id="pln_demo000001", decision="rejected", reason="comfort plan requires consent")
    assert PlanDecision.model_validate(decision.model_dump(mode="json")) == decision
    scene = SceneState(
        room="living_room",
        people=[
            {
                "posture": "sitting",
                "on_floor": False,
                "location_surface": "sofa",
                "gesture": "pointing",
                "attention_target": "lamp.living_floor",
            }
        ],
        objects_seen=[{"name": "keys", "location_desc": "on the table"}],
        text_present=False,
        lighting="bright",
        summary="A person on the sofa points at the floor lamp.",
        confidence=0.91,
        extra_note="kept",
    )
    assert scene.model_dump()["extra_note"] == "kept"
    assert scene_schema()["title"] == "SceneState"
