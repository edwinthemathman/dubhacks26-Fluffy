"""Plan contract shared with the inference and demo parts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from fluffy.tools.registry import BoundTools, ToolArgumentError

SCHEMA_VERSION = "1.0"
PLAN_TTL = timedelta(seconds=30)
ActionClass = Literal["comfort", "notify", "safety"]
_CLASS_RANK = {"notify": 0, "safety": 1, "comfort": 2}


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class PlanValidationError(ValueError):
    """A plan broke the frozen contract or named a tool incorrectly."""


class Trigger(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["spoken", "inferred", "sound", "safety"]
    utterance: str | None = None
    event: str | None = None
    room: str | None = None


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: str = Field(pattern=r"^act_[A-Za-z0-9_-]+$")
    tool: str
    args: dict[str, Any]


class Consent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    required: bool
    given_by: Literal["nod", "voice", "button", "typing", "spoken_request", "profile"] | None = None
    at: datetime | None = None
    question: str | None = None

    @field_validator("at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _as_utc(value)

    @model_validator(mode="after")
    def _given_by_has_time(self) -> Consent:
        if self.given_by is not None and self.at is None:
            raise ValueError("consent.at is required when consent.given_by is set")
        return self


class PlanProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    needs: list[str] = Field(default_factory=list)
    delivered_via: list[str] = Field(default_factory=list)


class Evidence(BaseModel):
    """Evidence is open beyond ``kind`` so Part 2 can attach its own fields."""

    model_config = ConfigDict(extra="allow")
    kind: Literal["speech", "vision", "sound", "state"]


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: str = Field(pattern=r"^pln_[A-Za-z0-9_-]+$")
    created_at: datetime
    expires_at: datetime
    trigger: Trigger
    action_class: ActionClass
    intent: str = Field(min_length=1)
    actions: list[Action] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    consent: Consent
    profile: PlanProfile = Field(default_factory=PlanProfile)
    reasoning_summary: str = ""
    evidence: list[Evidence] = Field(default_factory=list)

    @field_validator("created_at", "expires_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return _as_utc(value)

    @model_validator(mode="after")
    def _expiry_after_creation(self) -> Plan:
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be later than created_at")
        ids = [action.action_id for action in self.actions]
        if len(ids) != len(set(ids)):
            raise ValueError("action_id values must be unique")
        return self


class PlanProposed(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["plan.proposed"] = "plan.proposed"
    schema_version: Literal["1.0"] = "1.0"
    plan: Plan


class PlanDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["plan.decision"] = "plan.decision"
    plan_id: str
    decision: Literal["approved", "rejected"]
    reason: str = Field(min_length=1)


class ActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: str
    status: Literal["ok", "error", "skipped"]
    error: str | None = None
    previous_state: dict[str, Any]
    new_state: dict[str, Any]


class PlanResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["plan.result"] = "plan.result"
    plan_id: str
    status: Literal["succeeded", "partial", "failed"]
    action_results: list[ActionResult]


class PlanCancel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["plan.cancel"] = "plan.cancel"
    plan_id: str
    reason: str = Field(min_length=1)


class ExecutorStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["executor.status"] = "executor.status"
    connected: bool
    status: str
    name: str | None = None
    version: str | None = None


class CaregiverAlert(BaseModel):
    """A mock alert. This subsystem never attaches a snapshot image."""

    model_config = ConfigDict(extra="forbid")
    alert_id: str
    severity: Literal["info", "high"]
    message: str
    include_snapshot: bool = False
    snapshot: None = None
    acknowledged: bool
    acknowledged_at: datetime | None = None
    created_at: datetime
    source: str

    @field_validator("created_at", "acknowledged_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _as_utc(value)


class CaregiverAlertMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["caregiver.alert"] = "caregiver.alert"
    plan_id: str
    alert: CaregiverAlert


class Hello(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["hello"] = "hello"
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)


def strictest_class(classes: list[ActionClass]) -> ActionClass:
    return max(classes, key=lambda item: _CLASS_RANK[item])


def normalize_plan(message: PlanProposed, bound: BoundTools) -> PlanProposed:
    """Validate tools and cap expiry at 30 seconds from creation."""

    plan = message.plan
    if message.schema_version != SCHEMA_VERSION:
        raise PlanValidationError("unsupported schema_version")
    created = plan.created_at
    expires = plan.expires_at
    cap = created + PLAN_TTL
    if expires > cap:
        expires = cap
    classes: list[ActionClass] = []
    actions: list[Action] = []
    try:
        for action in plan.actions:
            args = bound.parse_plan_arguments(action.tool, action.args)
            classes.append(bound.action_class(action.tool))
            actions.append(action.model_copy(update={"args": args}))
    except ToolArgumentError as exc:
        raise PlanValidationError(str(exc)) from exc
    expected = strictest_class(classes)
    if plan.action_class != expected:
        raise PlanValidationError(f"action_class must be {expected}")
    if expected == "comfort" and not plan.consent.required:
        raise PlanValidationError("comfort plans require consent")
    updated = plan.model_copy(update={"expires_at": expires, "actions": actions})
    return message.model_copy(update={"plan": updated})


def wire_schema() -> dict[str, Any]:
    """JSON Schema for the messages this subsystem sends on the executor link."""

    from pydantic import TypeAdapter

    adapter = TypeAdapter(
        PlanProposed
        | PlanDecision
        | PlanResult
        | PlanCancel
        | ExecutorStatus
        | CaregiverAlertMessage
        | Hello
    )
    schema = adapter.json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "FluffyExecutorLink"
    schema["description"] = (
        "Messages for the executor link, the UI stream, and the caregiver channel. "
        "A plan expires 30 seconds after created_at if it has not been decided."
    )
    return schema


def parse_wire(message: dict[str, Any]) -> Any:
    """Validate one link message."""

    models = (
        PlanProposed,
        PlanDecision,
        PlanResult,
        PlanCancel,
        ExecutorStatus,
        CaregiverAlertMessage,
        Hello,
    )
    kind = message.get("type")
    for model in models:
        if kind == model.model_fields["type"].default:
            try:
                return model.model_validate(message)
            except ValidationError as exc:
                raise PlanValidationError(_validation_text(exc)) from exc
    raise PlanValidationError(f"unknown message type: {kind}")


def _validation_text(exc: ValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors():
        loc = ".".join(str(item) for item in error["loc"])
        parts.append(f"{loc}: {error['msg']}" if loc else error["msg"])
    return "; ".join(parts)
