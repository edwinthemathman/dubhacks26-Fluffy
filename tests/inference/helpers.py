"""Shared fixtures for inference tests."""

from pathlib import Path

from fluffy.clock import FrozenClock
from fluffy.ids import IdFactory
from fluffy.models import Action, Consent, Trigger, build_plan
from fluffy.profile import load_profiles
from fluffy.profile.model import Profile

ROOT = Path(__file__).resolve().parents[2]


def profiles() -> dict[str, Profile]:
    return load_profiles(ROOT / "config" / "profiles")


def dim_plan(
    clock: FrozenClock,
    *,
    kind: str = "inferred",
    confidence: float = 0.9,
    given_by: str | None = None,
    intent: str = "Dim the floor lamp",
    luminosity: int = 35,
):
    return build_plan(
        clock=clock,
        ids=IdFactory(),
        trigger=Trigger(
            kind=kind,  # type: ignore[arg-type]
            utterance="dim the lamp" if kind == "spoken" else None,
            room="living_room",
        ),
        action_class="comfort",
        intent=intent,
        actions=[
            Action(
                action_id="act_1",
                tool="dim_lights",
                args={
                    "room": "living_room",
                    "luminosity": luminosity,
                    "light": "lamp.living_floor",
                },
            )
        ],
        confidence=confidence,
        consent=Consent(required=True, given_by=given_by),  # type: ignore[arg-type]
        needs=["speech"],
        delivered_via=["speech"],
        reasoning_summary="test",
    )
