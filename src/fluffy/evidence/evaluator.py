"""Five-check evidence policy.

A numeric confidence score is not an input. The checks are heuristics, not
calibrated probabilities. Hard gates live outside this score.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from fluffy.config import HomeConfig
from fluffy.models.contracts import CheckOutcome, Observation


@dataclass
class ContextView:
    now: datetime
    cancelled: bool = False
    manual_targets: set[str] = field(default_factory=set)
    cooldown_until: datetime | None = None
    contradictory: bool = False


@dataclass
class EvidenceDecision:
    checks: list[CheckOutcome]
    score: int
    outcome: str  # automatic, ask, quiet
    detail: str


def _targets_for(tool: str, args: dict) -> list[str]:
    targets = []
    if args.get("room"):
        targets.append(args["room"])
    if args.get("light"):
        targets.append(args["light"])
    if tool == "set_blinds":
        targets.append(args.get("room", ""))
    return [item for item in targets if item]


def _fresh_limit(home: HomeConfig, trigger_event: str | None) -> timedelta:
    if trigger_event in {"clap", "sound"}:
        return timedelta(seconds=home.clap_fresh_s)
    return timedelta(seconds=home.room_cue_fresh_s)


def evaluate_action(
    home: HomeConfig,
    tool: str,
    args: dict,
    observations: list[Observation],
    context: ContextView,
    trigger_event: str | None,
    *,
    explicit: bool,
) -> EvidenceDecision:
    cited = observations
    targets = _targets_for(tool, args)
    limit = _fresh_limit(home, trigger_event)

    cue_ok = False
    cue_detail = "no observation supports this action"
    if explicit and any("explicit_request" in obs.supports or "spoken_request" in obs.supports for obs in cited):
        cue_ok = True
        cue_detail = "explicit directed request"
    elif trigger_event and any(trigger_event in obs.supports for obs in cited):
        cue_ok = True
        cue_detail = f"observation supports {trigger_event}"
    elif any(obs.supports for obs in cited) and trigger_event is None and explicit:
        cue_ok = True
        cue_detail = "explicit request"

    known_targets = set(home.room_ids + home.light_ids + [blind.id for room in home.rooms for blind in room.blinds])
    target_ok = bool(targets) and all(target in known_targets or target in home.room_ids for target in targets)
    if tool == "show_caption":
        target_ok = True
    if tool == "alert_caregiver":
        target_ok = True
    target_detail = "target is configured" if target_ok else "target is missing or not configured"

    skew = timedelta(seconds=2)
    fresh = bool(cited) and all(
        obs.captured_at <= context.now + skew and context.now - obs.captured_at <= limit for obs in cited
    )
    fresh_detail = "observations are inside the freshness window" if fresh else "observations are missing, expired, or timestamped in the future"

    blocked = []
    if context.cancelled:
        blocked.append("cancelled")
    if context.contradictory:
        blocked.append("contradictory cue")
    if context.cooldown_until and context.now < context.cooldown_until:
        blocked.append("cooldown")
    if any(target in context.manual_targets for target in targets):
        blocked.append("manual change")
    context_ok = not blocked
    context_detail = "context agrees" if context_ok else "blocked by " + ", ".join(blocked)

    kinds = {obs.kind for obs in cited}
    timestamps = {obs.captured_at for obs in cited}
    explicit_support = explicit and any(
        "explicit_request" in obs.supports or "spoken_request" in obs.supports for obs in cited
    )
    corroboration = len(kinds) >= 2
    sequence = len(timestamps) >= 2 and len(cited) >= 2
    support_ok = explicit_support or corroboration or sequence
    if support_ok and explicit_support:
        support_detail = "explicit directed request"
    elif corroboration:
        support_detail = "corroborating modalities"
    elif sequence:
        support_detail = "temporal sequence"
    else:
        support_detail = "support is not sufficient"

    checks = [
        CheckOutcome(name="cue_supported", passed=cue_ok, detail=cue_detail),
        CheckOutcome(name="target_clear", passed=target_ok, detail=target_detail),
        CheckOutcome(name="evidence_fresh", passed=fresh, detail=fresh_detail),
        CheckOutcome(name="context_agrees", passed=context_ok, detail=context_detail),
        CheckOutcome(name="support_sufficient", passed=support_ok, detail=support_detail),
    ]
    score = sum(1 for check in checks if check.passed)
    if score == 5 and home.automatic_comfort:
        outcome = "automatic"
    elif score >= 3:
        outcome = "ask"
    else:
        outcome = "quiet"
    if explicit and score == 5:
        outcome = "automatic"
    return EvidenceDecision(checks=checks, score=score, outcome=outcome, detail=f"{score}/5 -> {outcome}")
