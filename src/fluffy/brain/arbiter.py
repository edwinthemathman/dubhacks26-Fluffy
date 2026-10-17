"""Checks a plan before it can leave inference.

Comfort plans need consent unless a clear cue already supplied it.
A caregiver call always waits for a yes. Offers are limited to one every
two minutes, and a declined offer stays declined for 30 minutes.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from fluffy.clock import Clock
from fluffy.models import Plan

LOW_CONFIDENCE = 0.7
OFFER_WINDOW = timedelta(minutes=2)
DECLINE_WINDOW = timedelta(minutes=30)

VerdictStatus = Literal["allow", "needs_consent", "considered", "drop"]


@dataclass(frozen=True)
class Verdict:
    status: VerdictStatus
    reason: str


def offer_key(plan: Plan) -> str:
    parts = []
    for action in plan.actions:
        args = json.dumps(action.args, sort_keys=True)
        parts.append(f"{action.tool}:{args}")
    return "|".join(parts)


class Arbiter:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.offered_at: list[datetime] = []
        self.declined: dict[str, datetime] = {}

    def review(self, plan: Plan) -> Verdict:
        if _calls_caregiver(plan):
            if plan.consent.given_by is None:
                return Verdict("needs_consent", "ask before calling for help")
            return Verdict("allow", "caregiver call was accepted")
        if plan.action_class != "comfort":
            return Verdict("allow", "notify is pre-agreed")
        if plan.consent.given_by is not None:
            return Verdict("allow", "consent already given")

        inferred = plan.trigger.kind == "inferred"
        if self._declined_recently(plan):
            return Verdict("drop", "this offer was declined in the last 30 minutes")
        if inferred and self._offer_rate_limited():
            return Verdict("drop", "already offered something in the last 2 minutes")
        if inferred and plan.confidence < LOW_CONFIDENCE:
            return Verdict("needs_consent", "confidence below 0.7, so ask first")
        return Verdict("needs_consent", "comfort changes wait for a yes")

    def note_offer(self) -> None:
        now = self.clock.now()
        self.offered_at.append(now)
        cutoff = now - OFFER_WINDOW
        self.offered_at = [at for at in self.offered_at if at >= cutoff]

    def note_decline(self, plan: Plan) -> None:
        self.declined[offer_key(plan)] = self.clock.now()

    def _declined_recently(self, plan: Plan) -> bool:
        declined_at = self.declined.get(offer_key(plan))
        if declined_at is None:
            return False
        return self.clock.now() - declined_at < DECLINE_WINDOW

    def _offer_rate_limited(self) -> bool:
        cutoff = self.clock.now() - OFFER_WINDOW
        return any(at >= cutoff for at in self.offered_at)


def _calls_caregiver(plan: Plan) -> bool:
    return any(action.tool == "alert_caregiver" for action in plan.actions)
