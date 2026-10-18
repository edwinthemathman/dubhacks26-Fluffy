"""Revalidate a plan against a harness authorization record.

Confidence numbers and browser-supplied authorization ids are not accepted here.
The record must already have been loaded from the harness.
"""

from __future__ import annotations

from datetime import datetime

from fluffy.authz.store import action_hash
from fluffy.models.contracts import AuthorizationRecord, Plan


def authorize_plan(plan: Plan, record: AuthorizationRecord | None, now: datetime, cancelled: bool) -> tuple[str, str]:
    if cancelled:
        return "rejected", "plan_cancelled"
    if now >= plan.expires_at:
        return "rejected", "plan_expired"
    if record is None:
        return "rejected", "authorization_missing"
    if record.plan_id != plan.plan_id or record.session_id != plan.session_id:
        return "rejected", "authorization_mismatch"
    if record.action_hash != action_hash(plan.session_id, plan.actions):
        return "rejected", "authorization_mismatch"
    if record.expires_at <= now:
        return "rejected", "authorization_expired"
    expected = {action.action_id: action.action_class for action in plan.actions}
    if expected != record.action_classes:
        return "rejected", "authorization_mismatch"
    return "approved", "authorized"
