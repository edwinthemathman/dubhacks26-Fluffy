"""Effect ledger keyed by session, plan, and action.

A repeated key returns the recorded outcome. A different payload under the same
key is rejected. In-flight external work becomes unknown after a restart.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fluffy.models.contracts import ActionResult


EXTERNAL_KINDS = {"browser_audio", "spotify", "photon"}


@dataclass
class LedgerEntry:
    session_id: str
    plan_id: str
    action_id: str
    payload_hash: str
    status: str
    effect_kind: str
    result: ActionResult | None
    provider_reference: str | None


class LedgerRejected(Exception):
    def __init__(self, error_code: str, message: str) -> None:
        self.error_code = error_code
        self.message = message
        super().__init__(message)


class EffectLedger:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS effects (
                session_id TEXT NOT NULL,
                plan_id TEXT NOT NULL,
                action_id TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                effect_kind TEXT NOT NULL,
                result_json TEXT,
                provider_reference TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                PRIMARY KEY (session_id, plan_id, action_id)
            )
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def get(self, session_id: str, plan_id: str, action_id: str) -> LedgerEntry | None:
        row = self._conn.execute(
            "SELECT * FROM effects WHERE session_id=? AND plan_id=? AND action_id=?",
            (session_id, plan_id, action_id),
        ).fetchone()
        if row is None:
            return None
        result = ActionResult.model_validate_json(row["result_json"]) if row["result_json"] else None
        return LedgerEntry(
            session_id=row["session_id"],
            plan_id=row["plan_id"],
            action_id=row["action_id"],
            payload_hash=row["payload_hash"],
            status=row["status"],
            effect_kind=row["effect_kind"],
            result=result,
            provider_reference=row["provider_reference"],
        )

    def begin(
        self,
        session_id: str,
        plan_id: str,
        action_id: str,
        payload_hash: str,
        effect_kind: str,
        started_at: datetime,
    ) -> tuple[LedgerEntry, bool]:
        existing = self.get(session_id, plan_id, action_id)
        if existing is not None:
            if existing.payload_hash != payload_hash:
                raise LedgerRejected(
                    "duplicate_payload_mismatch",
                    "this action id was already used with different arguments",
                )
            return existing, False
        self._conn.execute(
            """
            INSERT INTO effects (
                session_id, plan_id, action_id, payload_hash, status, effect_kind,
                result_json, provider_reference, started_at, finished_at
            ) VALUES (?, ?, ?, ?, 'started', ?, NULL, NULL, ?, NULL)
            """,
            (session_id, plan_id, action_id, payload_hash, effect_kind, _iso(started_at)),
        )
        self._conn.commit()
        entry = self.get(session_id, plan_id, action_id)
        assert entry is not None
        return entry, True

    def finish(self, result: ActionResult, payload_hash: str) -> ActionResult:
        existing = self.get(result.session_id, result.plan_id, result.action_id)
        if existing is None:
            raise LedgerRejected("ledger_missing", "action was not recorded before dispatch")
        if existing.payload_hash != payload_hash:
            raise LedgerRejected("duplicate_payload_mismatch", "payload hash changed")
        if existing.result is not None and existing.status != "started":
            return existing.result
        self._conn.execute(
            """
            UPDATE effects
            SET status=?, result_json=?, provider_reference=?, finished_at=?
            WHERE session_id=? AND plan_id=? AND action_id=?
            """,
            (
                result.status,
                result.model_dump_json(),
                result.provider_reference,
                _iso(result.finished_at or datetime.now(timezone.utc)),
                result.session_id,
                result.plan_id,
                result.action_id,
            ),
        )
        self._conn.commit()
        return result

    def reconcile_inflight(self, finished_at: datetime) -> list[ActionResult]:
        """Mark crashed in-flight work unknown. Do not resend it."""
        rows = self._conn.execute("SELECT * FROM effects WHERE status='started'").fetchall()
        results: list[ActionResult] = []
        for row in rows:
            result = ActionResult(
                action_id=row["action_id"],
                plan_id=row["plan_id"],
                session_id=row["session_id"],
                tool="unknown",
                status="unknown",
                effect_kind=row["effect_kind"],
                verification="unverified",
                started_at=_parse(row["started_at"]),
                finished_at=finished_at,
                provider_reference=row["provider_reference"],
                error_code="provider_unknown",
                detail="in-flight effect was not finished before restart",
            )
            self._conn.execute(
                """
                UPDATE effects
                SET status='unknown', result_json=?, finished_at=?
                WHERE session_id=? AND plan_id=? AND action_id=?
                """,
                (
                    result.model_dump_json(),
                    _iso(finished_at),
                    row["session_id"],
                    row["plan_id"],
                    row["action_id"],
                ),
            )
            results.append(result)
        self._conn.commit()
        return results


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)
