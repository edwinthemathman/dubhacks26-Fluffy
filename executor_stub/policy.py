"""Consent rules for the stub executor.

Comfort plans need ``consent.given_by``. Notify and safety plans are already
agreed in the profile, so they are approved immediately.
"""

from __future__ import annotations

from fluffy.models.plan import Plan


def decide(plan: Plan) -> tuple[str, str]:
    if plan.action_class == "comfort" and plan.consent.given_by is None:
        return "rejected", "comfort plan requires consent"
    if plan.action_class == "comfort":
        return "approved", "comfort plan has consent"
    if plan.action_class == "notify":
        return "approved", "notify plan pre-agreed in the profile"
    if plan.action_class == "safety":
        return "approved", "safety plan pre-agreed in the profile"
    return "rejected", f"unsupported action class: {plan.action_class}"
