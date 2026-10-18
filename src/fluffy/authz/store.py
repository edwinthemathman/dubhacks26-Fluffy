"""Harness-owned authorization records.

The model cannot mint these. The executor resolves them over an internal route
and rejects a hash mismatch.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from fluffy.models.contracts import AuthorizationRecord, PlanAction


def action_hash(session_id: str, actions: list[PlanAction]) -> str:
    payload = {
        "session_id": session_id,
        "actions": [
            {
                "action_id": action.action_id,
                "tool": action.tool,
                "args": action.args,
                "evidence_ids": list(action.evidence_ids),
            }
            for action in actions
        ],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class AuthorizationStore:
    def __init__(self) -> None:
        self._records: dict[str, AuthorizationRecord] = {}

    def put(self, record: AuthorizationRecord) -> AuthorizationRecord:
        self._records[record.authorization_id] = record
        return record

    def get(self, authorization_id: str, now: datetime) -> AuthorizationRecord | None:
        record = self._records.get(authorization_id)
        if record is None:
            return None
        if record.expires_at <= now:
            return None
        return record
