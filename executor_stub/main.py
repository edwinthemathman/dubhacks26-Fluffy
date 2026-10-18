"""Executor process. Receives plans, checks authorization, calls the output service."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone

import httpx
import websockets

from fluffy.config import RuntimeSettings, load_settings
from fluffy.ledger.sqlite import EffectLedger, LedgerRejected
from fluffy.models.contracts import (
    ActionResult,
    AuthorizationRecord,
    PlanProposed,
    PlanResult,
    SubResult,
    ToolEffect,
    aggregate_plan_status,
    parse_schema_version,
)
from fluffy.models.contracts import SchemaVersionError
from fluffy.tools.registry import build_registry
from executor_stub.policy import authorize_plan


def payload_hash(tool: str, args: dict) -> str:
    raw = json.dumps({"tool": tool, "args": args}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ResourceGate:
    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._latest: dict[str, str] = {}

    def key(self, tool: str, args: dict, action_id: str) -> str:
        room = str(args.get("room", ""))
        if tool in {"dim_lights", "set_light_warmth"}:
            return f"light:{room}:{args.get('light') or '*'}"
        if tool == "set_blinds":
            return f"blinds:{room}"
        if tool in {"play_sound", "stop_sound", "set_volume"}:
            return f"media:{room}"
        if tool == "alert_caregiver":
            return "caregiver"
        return f"solo:{action_id}"

    def supersedes(self, tool: str) -> bool:
        return tool in {"dim_lights", "set_light_warmth", "set_blinds", "set_volume"}

    async def acquire(self, tool: str, args: dict, action_id: str):
        key = self.key(tool, args, action_id)
        lock = self._locks.setdefault(key, asyncio.Lock())
        self._latest[key] = action_id
        await lock.acquire()
        return key, lock

    def stale(self, key: str, action_id: str, tool: str) -> bool:
        return self.supersedes(tool) and self._latest.get(key) != action_id


async def run_executor(settings: RuntimeSettings) -> None:
    ledger = EffectLedger(settings.ledger_path)
    reconciled = ledger.reconcile_inflight(datetime.now(timezone.utc))
    registry = build_registry(settings.home)
    headers = {"Authorization": f"Bearer {settings.internal_token}"}
    gate = ResourceGate()
    ws_url = settings.harness_url.replace("http://", "ws://") + "/ws/executor"
    async with httpx.AsyncClient(headers=headers) as client:
        while True:
            try:
                socket = await websockets.connect(ws_url, additional_headers=headers)
            except (OSError, websockets.exceptions.WebSocketException):
                await asyncio.sleep(0.5)
                continue
            try:
                await _serve_executor(socket, client, settings, registry, ledger, gate, reconciled)
            except websockets.exceptions.ConnectionClosed:
                pass
            reconciled = []
            await asyncio.sleep(0.5)


async def _serve_executor(socket, client, settings, registry, ledger, gate, reconciled) -> None:
    try:
        async with socket:
            await socket.send(json.dumps({"type": "executor.hello", "schema_version": "2.0", "name": "executor_stub"}))
            for result in reconciled:
                await socket.send(json.dumps({"type": "action.result", "schema_version": "2.0", "result": json.loads(result.model_dump_json())}))
            cancels: dict[str, asyncio.Event] = {}
            try:
                async for raw in socket:
                    message = json.loads(raw)
                    kind = message.get("type")
                    if kind == "plan.cancel":
                        event = cancels.get(message.get("plan_id", ""))
                        if event is not None:
                            event.set()
                        await client.post(f"{settings.home_url}/internal/cancel-audio")
                    elif kind == "plan.proposed":
                        try:
                            plan_id = message["plan"]["plan_id"]
                        except KeyError:
                            continue
                        event = asyncio.Event()
                        cancels[plan_id] = event
                        asyncio.create_task(
                            _execute(socket, client, settings, registry, ledger, gate, message, event)
                        )
            except websockets.exceptions.ConnectionClosed:
                return
    finally:
        return


async def _execute(socket, client, settings, registry, ledger, gate, message, cancel_event) -> None:
    now = datetime.now(timezone.utc)
    try:
        parse_schema_version(message)
        proposed = PlanProposed.model_validate(message)
    except (SchemaVersionError, Exception) as exc:
        code = exc.version if isinstance(exc, SchemaVersionError) else "invalid_arguments"  # type: ignore[attr-defined]
        if isinstance(exc, SchemaVersionError):
            code = "unsupported_schema_version"
        await _send(socket, {"type": "plan.decision", "schema_version": "2.0", "plan_id": message.get("plan", {}).get("plan_id"), "decision": "rejected", "reason": code})
        return
    plan = proposed.plan
    record = await _fetch_auth(client, settings, plan.authorization_id)
    decision, reason = authorize_plan(plan, record, now, cancel_event.is_set())
    await _send(
        socket,
        {
            "type": "plan.decision",
            "schema_version": "2.0",
            "plan_id": plan.plan_id,
            "session_id": plan.session_id,
            "decision": decision,
            "reason": reason,
        },
    )
    results: list[ActionResult] = []
    if decision != "approved":
        for action in plan.actions:
            results.append(
                _terminal(
                    action,
                    plan,
                    "cancelled" if reason == "plan_cancelled" else "failed",
                    "simulated_state",
                    reason,
                )
            )
        await _finish_plan(socket, plan, results)
        return
    for action in plan.actions:
        now = datetime.now(timezone.utc)
        if cancel_event.is_set() or now >= plan.expires_at:
            code = "plan_cancelled" if cancel_event.is_set() else "plan_expired"
            results.append(_terminal(action, plan, "cancelled" if cancel_event.is_set() else "skipped", _kind(registry, action.tool), code))
            continue
        digest = payload_hash(action.tool, action.args)
        try:
            entry, created = ledger.begin(
                plan.session_id,
                plan.plan_id,
                action.action_id,
                digest,
                _kind(registry, action.tool),
                now,
            )
        except LedgerRejected as exc:
            results.append(_terminal(action, plan, "failed", _kind(registry, action.tool), exc.error_code))
            continue
        if not created:
            if entry.result is not None:
                results.append(entry.result)
            else:
                results.append(_terminal(action, plan, "unknown", _kind(registry, action.tool), "provider_unknown"))
            continue
        key, lock = await gate.acquire(action.tool, action.args, action.action_id)
        try:
            if cancel_event.is_set() or gate.stale(key, action.action_id, action.tool):
                code = "plan_cancelled" if cancel_event.is_set() else "superseded"
                result = _terminal(
                    action,
                    plan,
                    "cancelled" if cancel_event.is_set() else "skipped",
                    _kind(registry, action.tool),
                    code,
                )
            elif action.tool == "alert_caregiver":
                claim = await client.post(
                    f"{settings.harness_url}/internal/incidents/{action.args.get('incident_id')}/claim",
                    json={"plan_id": plan.plan_id, "action_id": action.action_id},
                )
                if claim.status_code != 200 or not claim.json().get("eligible"):
                    result = _terminal(action, plan, "skipped", "photon", "incident_not_eligible")
                else:
                    result = await _started_dispatch(socket, client, settings, action, plan, cancel_event)
            else:
                result = await _started_dispatch(socket, client, settings, action, plan, cancel_event)
        finally:
            lock.release()
        ledger.finish(result, digest)
        results.append(result)
        await _send(socket, {"type": "action.result", "schema_version": "2.0", "result": json.loads(result.model_dump_json())})
    await _finish_plan(socket, plan, results)


async def _started_dispatch(socket, client, settings, action, plan, cancel_event) -> ActionResult:
    await _send(
        socket,
        {
            "type": "action.started",
            "schema_version": "2.0",
            "plan_id": plan.plan_id,
            "session_id": plan.session_id,
            "action_id": action.action_id,
            "tool": action.tool,
        },
    )
    return await _dispatch(client, settings, action, plan, cancel_event)


async def _dispatch(client, settings, action, plan, cancel_event) -> ActionResult:
    timeout = _timeout(settings, action.tool)
    started = datetime.now(timezone.utc)
    try:
        response = await client.post(
            f"{settings.home_url}/api/tools/{action.tool}",
            json=action.args,
            headers={
                "X-Plan-Id": plan.plan_id,
                "X-Action-Id": action.action_id,
                "X-Session-Id": plan.session_id,
            },
            timeout=timeout,
        )
    except httpx.TimeoutException:
        return _terminal(action, plan, "unknown", _kind_name(action.tool), "provider_timeout", started)
    except httpx.HTTPError:
        return _terminal(action, plan, "failed", _kind_name(action.tool), "connection_error", started)
    if response.status_code >= 400:
        code = "invalid_arguments"
        try:
            code = response.json().get("detail", {}).get("error_code", code)
        except Exception:
            pass
        return _terminal(action, plan, "failed", _kind_name(action.tool), code, started)
    effect = ToolEffect.model_validate(response.json())
    if cancel_event.is_set() and effect.status == "pending":
        effect.status = "cancelled"
        effect.error_code = "plan_cancelled"
    return ActionResult(
        action_id=action.action_id,
        plan_id=plan.plan_id,
        session_id=plan.session_id,
        tool=action.tool,
        status=effect.status,
        effect_kind=effect.effect_kind,
        verification=effect.verification,
        previous_state=effect.previous_state,
        new_state=effect.new_state,
        started_at=started,
        finished_at=datetime.now(timezone.utc),
        provider_reference=effect.provider_reference,
        error_code=effect.error_code,
        detail=effect.detail,
        sub_results=effect.sub_results,
    )


def _timeout(settings: RuntimeSettings, tool: str) -> float:
    if tool in {"announce"}:
        return settings.home.speech_total_cap_s + 2
    if tool in {"play_sound", "stop_sound", "set_volume"}:
        return settings.home.spotify_timeout_s + settings.home.local_timeout_s
    if tool == "alert_caregiver":
        return settings.home.photon_timeout_s + 2
    return settings.home.local_timeout_s


def _kind(registry, tool: str) -> str:
    try:
        return registry.get(tool).effect_kind
    except Exception:
        return _kind_name(tool)


def _kind_name(tool: str) -> str:
    if tool == "announce":
        return "browser_audio"
    if tool in {"play_sound", "stop_sound", "set_volume"}:
        return "spotify"
    if tool == "alert_caregiver":
        return "photon"
    return "simulated_state"


def _terminal(action, plan, status, effect_kind, error_code, started: datetime | None = None) -> ActionResult:
    now = datetime.now(timezone.utc)
    return ActionResult(
        action_id=action.action_id,
        plan_id=plan.plan_id,
        session_id=plan.session_id,
        tool=action.tool,
        status=status,
        effect_kind=effect_kind,  # type: ignore[arg-type]
        verification="unverified",
        started_at=started or now,
        finished_at=now,
        error_code=error_code,
        sub_results=[],
    )


async def _finish_plan(socket, plan, results: list[ActionResult]) -> None:
    body = PlanResult(
        plan_id=plan.plan_id,
        session_id=plan.session_id,
        status=aggregate_plan_status(results),
        action_results=results,
    )
    await _send(socket, {"type": "plan.result", "schema_version": "2.0", "result": json.loads(body.model_dump_json())})


async def _fetch_auth(client, settings, authorization_id: str) -> AuthorizationRecord | None:
    response = await client.get(f"{settings.harness_url}/internal/authorizations/{authorization_id}")
    if response.status_code != 200:
        return None
    return AuthorizationRecord.model_validate(response.json())


async def _send(socket, payload: dict) -> None:
    await socket.send(json.dumps(payload))


def main() -> None:
    settings = load_settings()
    asyncio.run(run_executor(settings))


if __name__ == "__main__":
    main()
