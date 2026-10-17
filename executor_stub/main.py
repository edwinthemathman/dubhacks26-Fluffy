"""WebSocket client that applies the fixed consent rules on the mock home."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
import websockets
from pydantic import ValidationError

from executor_stub.policy import decide
from fluffy.config import HomeConfig, load_home
from fluffy.models.plan import (
    ActionResult,
    CaregiverAlert,
    CaregiverAlertMessage,
    Hello,
    PlanDecision,
    PlanProposed,
    PlanResult,
    PlanValidationError,
    normalize_plan,
)
from fluffy.tools.registry import BoundTools, ToolArgumentError, configure

logger = logging.getLogger(__name__)


async def run_stub(
    harness_ws: str,
    home_url: str,
    stop: asyncio.Event | None = None,
    *,
    home: HomeConfig | None = None,
    bound: BoundTools | None = None,
) -> None:
    """Connect to the harness, say hello, and execute decided plans until stopped."""

    stop = stop or asyncio.Event()
    bound = bound or configure(home or load_home())
    async with httpx.AsyncClient(timeout=5.0) as client:
        while not stop.is_set():
            try:
                async with websockets.connect(harness_ws) as socket:
                    send_lock = asyncio.Lock()

                    async def send(payload: dict[str, Any]) -> None:
                        async with send_lock:
                            await socket.send(json.dumps(payload))

                    await send(Hello(name="executor_stub", version="1.0").model_dump(mode="json"))
                    closer = asyncio.create_task(_close_when_stopped(socket, stop))
                    cancelled: set[str] = set()
                    try:
                        async for raw in socket:
                            if stop.is_set():
                                break
                            try:
                                message = json.loads(raw)
                            except json.JSONDecodeError:
                                continue
                            if not isinstance(message, dict):
                                continue
                            if message.get("type") == "plan.cancel":
                                plan_id = message.get("plan_id")
                                if isinstance(plan_id, str):
                                    cancelled.add(plan_id)
                                continue
                            if message.get("type") == "plan.proposed":
                                asyncio.create_task(
                                    _guarded_plan(
                                        send,
                                        message,
                                        home_url,
                                        bound,
                                        cancelled,
                                        client,
                                    )
                                )
                    finally:
                        closer.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("executor stub reconnecting: %s", exc)
                if stop.is_set():
                    break
                try:
                    await asyncio.wait_for(stop.wait(), timeout=0.5)
                except TimeoutError:
                    pass


async def _guarded_plan(
    send: Any,
    message: dict[str, Any],
    home_url: str,
    bound: BoundTools,
    cancelled: set[str],
    client: httpx.AsyncClient,
) -> None:
    try:
        await _handle_plan(send, message, home_url, bound, cancelled, client)
    except asyncio.CancelledError:
        raise
    except (websockets.ConnectionClosed, OSError) as exc:
        logger.debug("dropped a plan after the executor disconnected: %s", exc)


async def _handle_plan(
    send: Any,
    message: dict[str, Any],
    home_url: str,
    bound: BoundTools,
    cancelled: set[str],
    client: httpx.AsyncClient,
) -> None:
    proposed_id = ""
    plan_body = message.get("plan")
    if isinstance(plan_body, dict) and isinstance(plan_body.get("plan_id"), str):
        proposed_id = plan_body["plan_id"]
    try:
        proposed = normalize_plan(PlanProposed.model_validate(message), bound)
    except (ValidationError, PlanValidationError, ToolArgumentError) as exc:
        if proposed_id in cancelled:
            return
        await send(
            PlanDecision(
                plan_id=proposed_id or "pln_invalid",
                decision="rejected",
                reason=str(exc) or "invalid plan",
            ).model_dump(mode="json")
        )
        return
    plan = proposed.plan
    if plan.plan_id in cancelled:
        return
    decision, reason = decide(plan)
    await send(
        PlanDecision(plan_id=plan.plan_id, decision=decision, reason=reason).model_dump(mode="json")  # type: ignore[arg-type]
    )
    if decision != "approved":
        return
    results: list[ActionResult] = []
    for action in plan.actions:
        if plan.plan_id in cancelled:
            results.append(
                ActionResult(
                    action_id=action.action_id,
                    status="skipped",
                    error="cancelled",
                    previous_state={},
                    new_state={},
                )
            )
            continue
        results.append(await _call_tool(client, home_url, plan.plan_id, action.action_id, action.tool, action.args, send))
    statuses = [item.status for item in results]
    if statuses and all(item == "ok" for item in statuses):
        overall = "succeeded"
    elif any(item == "ok" for item in statuses):
        overall = "partial"
    else:
        overall = "failed"
    await send(
        PlanResult(plan_id=plan.plan_id, status=overall, action_results=results).model_dump(mode="json")  # type: ignore[arg-type]
    )


async def _call_tool(
    client: httpx.AsyncClient,
    home_url: str,
    plan_id: str,
    action_id: str,
    tool: str,
    args: dict[str, Any],
    send: Any,
) -> ActionResult:
    try:
        response = await client.post(
            f"{home_url.rstrip('/')}/api/tools/{tool}",
            json=args,
            headers={"X-Plan-Id": plan_id},
        )
    except httpx.HTTPError as exc:
        return ActionResult(
            action_id=action_id,
            status="error",
            error=str(exc),
            previous_state={},
            new_state={},
        )
    if response.status_code >= 400:
        return ActionResult(
            action_id=action_id,
            status="error",
            error=response.text,
            previous_state={},
            new_state={},
        )
    body = response.json()
    previous = body.get("previous_state") if isinstance(body.get("previous_state"), dict) else {}
    current = body.get("new_state") if isinstance(body.get("new_state"), dict) else {}
    if tool == "alert_caregiver" and isinstance(current.get("alert"), dict):
        alert = CaregiverAlert.model_validate(current["alert"])
        await send(CaregiverAlertMessage(plan_id=plan_id, alert=alert).model_dump(mode="json"))
    return ActionResult(
        action_id=action_id,
        status="ok",
        error=None,
        previous_state=previous,
        new_state=current,
    )


async def _close_when_stopped(socket: Any, stop: asyncio.Event) -> None:
    await stop.wait()
    await socket.close()
