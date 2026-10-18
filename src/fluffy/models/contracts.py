"""Version 2.0 plan, result, state, and UI event contracts.

Unsupported schema versions fail closed. Tool arguments live on each action;
authorization is never accepted from the caller.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = "2.0"

ActionStatus = Literal["succeeded", "pending", "failed", "skipped", "cancelled", "unknown"]
PlanStatus = Literal["running", "succeeded", "partial", "failed", "cancelled", "unknown"]
EffectKind = Literal["simulated_state", "browser_audio", "spotify", "photon"]
Verification = Literal[
    "state_committed",
    "playback_confirmed",
    "provider_accepted",
    "provider_delivered",
    "unverified",
]
AuthMode = Literal["automatic", "explicit_request", "confirmed", "safety_workflow"]
ActionClass = Literal["comfort", "notify", "safety"]
Readiness = Literal["configured", "needs_authorization", "ready", "unavailable"]


class SchemaVersionError(ValueError):
    def __init__(self, version: object) -> None:
        self.version = version
        super().__init__(f"unsupported schema_version {version!r}; expected {SCHEMA_VERSION}")


def parse_schema_version(payload: dict[str, Any]) -> str:
    version = payload.get("schema_version")
    if version != SCHEMA_VERSION:
        raise SchemaVersionError(version)
    return version


class Trigger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["sound", "speech", "vision", "safety", "developer"]
    event: str | None = None
    room: str | None = None
    utterance: str | None = None


class ActionProposal(BaseModel):
    """One validated tool call. Evidence ids point at harness observations."""

    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(pattern=r"^act_[A-Za-z0-9_\-]{4,64}$")
    tool: str = Field(min_length=1, max_length=64)
    args: dict[str, Any]
    evidence_ids: list[str] = Field(min_length=1)


class PlanAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str
    tool: str
    args: dict[str, Any]
    evidence_ids: list[str]
    action_class: ActionClass


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str
    session_id: str
    created_at: datetime
    expires_at: datetime
    trigger: Trigger
    intent: str
    actions: list[PlanAction]
    authorization_id: str
    reasoning_summary: str


class PlanProposed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["plan.proposed"] = "plan.proposed"
    schema_version: Literal["2.0"] = "2.0"
    plan: Plan


class PlanSubmission(BaseModel):
    """Inference-facing submission. Callers cannot attach authorization."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["2.0"]
    session_id: str
    intent: str = Field(min_length=1, max_length=240)
    trigger: Trigger
    actions: list[ActionProposal] = Field(min_length=1, max_length=8)
    reasoning_summary: str = Field(min_length=1, max_length=500)


class DeveloperRequest(BaseModel):
    """Labeled developer input. It enters the explicit-request path."""

    model_config = ConfigDict(extra="forbid")

    tool: str
    args: dict[str, Any]
    intent: str = Field(default="Developer explicit request", max_length=240)
    request_id: str | None = Field(default=None, pattern=r"^req_[A-Za-z0-9_\-]{4,64}$")


class CheckOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Literal[
        "cue_supported",
        "target_clear",
        "evidence_fresh",
        "context_agrees",
        "support_sufficient",
    ]
    passed: bool
    detail: str


class AuthorizationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authorization_id: str
    session_id: str
    plan_id: str
    action_hash: str
    evidence_ids: list[str]
    checks: list[CheckOutcome]
    score: int = Field(ge=0, le=5)
    mode: AuthMode
    action_classes: dict[str, ActionClass]
    expires_at: datetime
    created_at: datetime


class SubResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: str
    status: ActionStatus
    verification: Verification
    error_code: str | None = None
    provider_reference: str | None = None


class ActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str
    plan_id: str
    session_id: str
    tool: str
    status: ActionStatus
    effect_kind: EffectKind
    verification: Verification
    previous_state: dict[str, Any] | None = None
    new_state: dict[str, Any] | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    provider_reference: str | None = None
    error_code: str | None = None
    detail: str | None = None
    sub_results: list[SubResult] = Field(default_factory=list)


class ToolEffect(BaseModel):
    """What the output service returns for one tool call."""

    model_config = ConfigDict(extra="forbid")

    status: ActionStatus
    effect_kind: EffectKind
    verification: Verification
    previous_state: dict[str, Any] | None = None
    new_state: dict[str, Any] | None = None
    provider_reference: str | None = None
    error_code: str | None = None
    detail: str | None = None
    sub_results: list[SubResult] = Field(default_factory=list)
    message: OutputMessage | None = None


class PlanResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str
    session_id: str
    status: PlanStatus
    action_results: list[ActionResult]


class OutputMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str
    text: str
    priority: Literal["normal", "high"]
    created_at: datetime
    expires_at: datetime | None = None
    speak: bool = False
    audio_url: str | None = None
    playback: Literal["none", "generated", "started", "ended", "failed", "blocked"] = "none"
    source: str
    session_id: str


class LightState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    room: str
    label: str
    luminosity: int = Field(ge=0, le=100)
    warmth: Literal["warm", "neutral", "cool"]
    color: str


class BlindState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    room: str
    label: str
    position: int = Field(ge=0, le=100)


class MediaState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    room: str
    provider: Literal["none", "spotify", "local"] = "none"
    sound_id: str | None = None
    title: str | None = None
    volume: int | None = None
    playback: Literal["idle", "requesting", "playing", "paused", "error"] = "idle"
    error_code: str | None = None
    verification: Verification | None = None
    simulated: bool = True


class FlashOverlay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    room: str
    light_ids: list[str]
    color: Literal["blue", "amber", "white"]
    pulses: int = Field(ge=1, le=3)
    started_at: datetime
    ends_at: datetime
    on_ms: int = 600
    off_ms: int = 600


class Caption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    text: str
    priority: Literal["normal", "high"]
    created_at: datetime
    expires_at: datetime | None = None
    source: str
    resolved: bool = False
    message_id: str | None = None
    speak: bool = False
    audio_url: str | None = None
    playback: Literal["none", "generated", "started", "ended", "failed", "blocked"] = "none"


class AlertView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_id: str
    severity: Literal["info", "high"]
    message: str
    status: Literal["submitting", "accepted", "delivered", "failed", "unknown", "cancelled"]
    verification: Verification
    provider_reference: str | None = None
    error_code: str | None = None
    acknowledged: bool = False


class IncidentView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_id: str
    phase: str
    room: str
    audio_failed: bool = False
    question_id: str | None = None
    question_text: str | None = None


class HomeState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int
    updated_at: datetime
    rooms: list[str]
    lights: list[LightState]
    blinds: list[BlindState]
    media: list[MediaState]
    flashes: list[FlashOverlay]
    captions: list[Caption]
    alerts: list[AlertView]
    incident: IncidentView | None = None


class UiEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    session_id: str
    sequence: int
    timestamp: datetime
    type: str
    payload: dict[str, Any]


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: str
    session_id: str
    kind: Literal["audio", "video", "speech", "state", "manual"]
    captured_at: datetime
    room: str | None = None
    supports: list[str]
    targets: list[str]
    summary: str
    source: Literal["live", "fixture", "developer"]


class PossibleFallEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(pattern=r"^evt_[A-Za-z0-9_\-]{4,64}$")
    session_id: str
    room: str
    descent_at: datetime
    floor_at: datetime
    source: Literal["inference", "developer", "fixture"]

    @field_validator("descent_at", "floor_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        return value


class IntegrationStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["spotify", "elevenlabs", "photon"]
    readiness: Readiness
    detail: str


def aggregate_plan_status(results: list[ActionResult]) -> PlanStatus:
    if not results:
        return "failed"
    statuses = [item.status for item in results]
    if any(item.status == "failed" and item.error_code == "partial_effects" for item in results):
        if any(status == "succeeded" for status in statuses) or len(results) == 1:
            return "partial"
    if all(status == "succeeded" for status in statuses):
        return "succeeded"
    if all(status == "cancelled" for status in statuses):
        return "cancelled"
    if any(status == "succeeded" for status in statuses):
        return "partial"
    if any(status == "unknown" for status in statuses):
        return "unknown"
    if all(status in {"cancelled", "skipped"} for status in statuses):
        return "cancelled"
    return "failed"
