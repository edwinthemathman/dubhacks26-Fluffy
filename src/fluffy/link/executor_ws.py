"""WebSocket link between the harness, the stub executor, and the UI.

The harness never applies a tool. It validates a plan, holds it until an
executor decides or 30 seconds pass, and copies every link message to ``/ws/ui``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket
from pydantic import ValidationError

from fluffy.clock import Clock, SystemClock
from fluffy.models.plan import (
    CaregiverAlertMessage,
    ExecutorStatus,
    Hello,
    PlanCancel,
    PlanDecision,
    PlanProposed,
    PlanResult,
    PlanValidationError,
    normalize_plan,
)
from fluffy.tools.registry import BoundTools

logger = logging.getLogger(__name__)
OFFLINE = "executor offline"
ONLINE = "executor online"


@dataclass
class PlanRecord:
    proposed: PlanProposed
    decision: PlanDecision | None = None
    result: PlanResult | None = None
    cancelled: bool = False
    finished: asyncio.Event = field(default_factory=asyncio.Event)

    def public(self) -> dict[str, Any]:
        return {
            "plan": self.proposed.model_dump(mode="json"),
            "decision": None if self.decision is None else self.decision.model_dump(mode="json"),
            "result": None if self.result is None else self.result.model_dump(mode="json"),
            "cancelled": self.cancelled,
        }


class ExecutorLink:
    def __init__(self, bound: BoundTools, clock: Clock | None = None) -> None:
        self.bound = bound
        self.clock = clock or SystemClock()
        self.executor: WebSocket | None = None
        self.ready = False
        self.executor_name: str | None = None
        self.executor_version: str | None = None
        self.ui: dict[WebSocket, asyncio.Lock] = {}
        self.caregiver: dict[WebSocket, asyncio.Lock] = {}
        self.records: dict[str, PlanRecord] = {}
        self.recent: list[dict[str, Any]] = []
        self.last_home: dict[str, Any] | None = None
        self._executor_lock = asyncio.Lock()

    def status_message(self) -> dict[str, Any]:
        if self.ready:
            return ExecutorStatus(
                connected=True,
                status=ONLINE,
                name=self.executor_name,
                version=self.executor_version,
            ).model_dump(mode="json")
        return ExecutorStatus(connected=False, status=OFFLINE).model_dump(mode="json")

    async def submit(self, message: PlanProposed) -> PlanRecord:
        normalized = normalize_plan(message, self.bound)
        plan_id = normalized.plan.plan_id
        if plan_id in self.records:
            raise PlanValidationError(f"duplicate plan_id: {plan_id}")
        record = PlanRecord(proposed=normalized)
        self.records[plan_id] = record
        payload = normalized.model_dump(mode="json")
        await self.broadcast_ui(payload)
        if normalized.plan.expires_at <= self.clock.now():
            await self._expire(record)
            return record
        if self.ready and self.executor is not None:
            await self._send(self.executor, payload, self._executor_lock)
        else:
            await self.broadcast_ui(self.status_message())
        return record

    async def cancel(self, plan_id: str, reason: str) -> PlanCancel:
        record = self.records.get(plan_id)
        if record is None:
            raise PlanValidationError(f"unknown plan: {plan_id}")
        if record.decision is not None or record.cancelled:
            raise PlanValidationError("plan has already been decided")
        record.cancelled = True
        record.finished.set()
        message = PlanCancel(plan_id=plan_id, reason=reason)
        payload = message.model_dump(mode="json")
        await self.broadcast_ui(payload)
        if self.ready and self.executor is not None:
            await self._send(self.executor, payload, self._executor_lock)
        return message

    async def sweep(self) -> None:
        now = self.clock.now()
        for record in list(self.records.values()):
            if record.decision is None and not record.cancelled and record.proposed.plan.expires_at <= now:
                await self._expire(record)

    async def connect_ui(self, websocket: WebSocket) -> None:
        await websocket.accept()
        lock = asyncio.Lock()
        self.ui[websocket] = lock
        await self._send(websocket, self.status_message(), lock)
        for message in list(self.recent):
            await self._send(websocket, message, lock)
        if self.last_home is not None:
            await self._send(websocket, self.last_home, lock)

    async def connect_caregiver(self, websocket: WebSocket) -> None:
        await websocket.accept()
        lock = asyncio.Lock()
        self.caregiver[websocket] = lock
        if self.last_home is not None and "state" in self.last_home:
            await self._send(
                websocket,
                {"type": "state.snapshot", "state": self.last_home["state"]},
                lock,
            )
        for message in list(self.recent):
            if message.get("type") == "caregiver.alert" or message.get("topic") == "caregiver":
                await self._send(websocket, message, lock)

    async def attach_executor(self, websocket: WebSocket) -> bool:
        await websocket.accept()
        if self.executor is not None:
            await websocket.send_json({"type": "error", "error": "executor already connected"})
            await websocket.close()
            return False
        self.executor = websocket
        self.ready = False
        self._executor_lock = asyncio.Lock()
        return True

    async def detach_executor(self, websocket: WebSocket) -> None:
        if self.executor is not websocket:
            return
        self.executor = None
        self.ready = False
        self.executor_name = None
        self.executor_version = None
        await self.broadcast_ui(self.status_message())

    async def on_executor_message(self, message: dict[str, Any]) -> None:
        """Copy every executor message to the UI, then apply fixed bookkeeping."""

        await self.broadcast_ui(message)
        kind = message.get("type")
        try:
            if kind == "hello":
                hello = Hello.model_validate(message)
            elif kind == "plan.decision":
                decision = PlanDecision.model_validate(message)
            elif kind == "plan.result":
                result = PlanResult.model_validate(message)
            elif kind == "caregiver.alert":
                alert = CaregiverAlertMessage.model_validate(message)
            else:
                return
        except ValidationError as exc:
            logger.warning("rejected executor message: %s", exc)
            if self.executor is not None:
                await self._send(
                    self.executor,
                    {"type": "error", "error": "invalid message"},
                    self._executor_lock,
                )
            return
        if kind == "hello":
            self.ready = True
            self.executor_name = hello.name
            self.executor_version = hello.version
            await self.broadcast_ui(self.status_message())
            await self._flush()
        elif kind == "plan.decision":
            record = self.records.get(decision.plan_id)
            if record is None or record.cancelled or record.decision is not None:
                return
            record.decision = decision
            if decision.decision == "rejected":
                record.finished.set()
        elif kind == "plan.result":
            record = self.records.get(result.plan_id)
            if record is None or record.result is not None:
                return
            if record.cancelled and record.decision is None:
                return
            record.result = result
            record.finished.set()
        elif kind == "caregiver.alert":
            await self.broadcast_caregiver(alert.model_dump(mode="json"))

    async def ingest_home(self, message: dict[str, Any]) -> None:
        if "state" in message:
            self.last_home = message
        await self.broadcast_ui(message)
        if message.get("topic") == "caregiver":
            await self.broadcast_caregiver(message)

    async def broadcast_ui(self, message: dict[str, Any]) -> None:
        self.recent.append(message)
        del self.recent[:-200]
        await self._fanout(self.ui, message)

    async def broadcast_caregiver(self, message: dict[str, Any]) -> None:
        await self._fanout(self.caregiver, message)

    async def disconnect(self, websocket: WebSocket) -> None:
        self.ui.pop(websocket, None)
        self.caregiver.pop(websocket, None)
        if self.executor is websocket:
            await self.detach_executor(websocket)

    async def _expire(self, record: PlanRecord) -> None:
        if record.decision is not None or record.cancelled:
            return
        decision = PlanDecision(
            plan_id=record.proposed.plan.plan_id,
            decision="rejected",
            reason="expired",
        )
        record.decision = decision
        record.finished.set()
        await self.broadcast_ui(decision.model_dump(mode="json"))

    async def _flush(self) -> None:
        if self.executor is None:
            return
        now = self.clock.now()
        for record in self.records.values():
            plan = record.proposed.plan
            if record.decision is None and not record.cancelled and plan.expires_at > now:
                await self._send(self.executor, record.proposed.model_dump(mode="json"), self._executor_lock)

    async def _fanout(self, clients: dict[WebSocket, asyncio.Lock], message: dict[str, Any]) -> None:
        for websocket, lock in list(clients.items()):
            await self._send(websocket, message, lock)

    async def _send(self, websocket: WebSocket, message: dict[str, Any], lock: asyncio.Lock) -> None:
        try:
            async with lock:
                await websocket.send_json(message)
        except Exception:
            await self.disconnect(websocket)
