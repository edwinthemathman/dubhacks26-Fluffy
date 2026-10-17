"""Plan contract produced by inference and consumed by the executor.

Part 1 owns this shape (`01-execution.md`). These models match that write-up
so this branch can build and test plans before the execution code is merged.
`given_by` includes `pointing`, which the written enum does not list yet.
"""

from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from fluffy.clock import Clock
from fluffy.ids import IdFactory

ActionClass = Literal["comfort", "notify", "safety"]
TriggerKind = Literal["spoken", "inferred", "sound", "safety"]
ConsentVia = Literal[
    "nod",
    "voice",
    "button",
    "typing",
    "pointing",
    "spoken_request",
    "profile",
]


class Trigger(BaseModel):
    kind: TriggerKind
    utterance: str | None = None
    event: str | None = None
    room: str = "living_room"


class Consent(BaseModel):
    required: bool
    given_by: ConsentVia | None = None
    at: datetime | None = None
    question: str | None = None


class Action(BaseModel):
    action_id: str
    tool: str
    args: dict[str, Any]


class ProfileDelivery(BaseModel):
    needs: list[str] = Field(default_factory=list)
    delivered_via: list[str] = Field(default_factory=list)


class Evidence(BaseModel):
    kind: Literal["speech", "vision", "sound", "state"]
    text: str | None = None
    summary: str | None = None
    at: datetime | None = None


class Plan(BaseModel):
    plan_id: str
    created_at: datetime
    expires_at: datetime
    trigger: Trigger
    action_class: ActionClass
    intent: str
    actions: list[Action]
    confidence: float
    consent: Consent
    profile: ProfileDelivery
    reasoning_summary: str
    evidence: list[Evidence] = Field(default_factory=list)
    # Inference doc requires this on every decision. Part 1's schema does not list it yet.
    context_snapshot_id: str | None = None


def build_plan(
    *,
    clock: Clock,
    ids: IdFactory,
    trigger: Trigger,
    action_class: ActionClass,
    intent: str,
    actions: list[Action],
    confidence: float,
    consent: Consent,
    needs: list[str],
    delivered_via: list[str],
    reasoning_summary: str,
    evidence: list[Evidence] | None = None,
    snapshot_id: str | None = None,
) -> Plan:
    now = clock.now()
    return Plan(
        plan_id=ids("pln"),
        created_at=now,
        expires_at=now + timedelta(seconds=30),
        trigger=trigger,
        action_class=action_class,
        intent=intent,
        actions=actions,
        confidence=confidence,
        consent=consent,
        profile=ProfileDelivery(needs=list(needs), delivered_via=list(delivered_via)),
        reasoning_summary=reasoning_summary,
        evidence=list(evidence or []),
        context_snapshot_id=snapshot_id,
    )
