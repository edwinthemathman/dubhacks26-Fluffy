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
    blocked = _caregiver_rules(plan)
    if blocked:
        return "rejected", blocked
    return "approved", "authorized"


def _caregiver_rules(plan: Plan) -> str | None:
    """Second line for caregiver texts. Rules only: the safety machine already decided."""

    for action in plan.actions:
        if action.tool != "alert_caregiver":
            continue
        if plan.trigger.kind != "safety":
            return "caregiver_not_from_safety"
        incident = str(action.args.get("incident_id") or "")
        if not incident.startswith("inc_"):
            return "caregiver_missing_incident"
        if not action.evidence_ids:
            return "caregiver_missing_evidence"
    return None
