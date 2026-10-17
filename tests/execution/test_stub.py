import asyncio
import json
import socket
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import uvicorn
import websockets

from executor_stub.main import run_stub
from executor_stub.policy import decide
from fluffy.clock import FrozenClock
from fluffy.config import load_home
from fluffy.link.executor_ws import OFFLINE, ExecutorLink
from fluffy.models.plan import (
    PLAN_TTL,
    Action,
    Consent,
    Plan,
    PlanProposed,
    Trigger,
)
from fluffy.server.app import Settings, create_app
from fluffy.tools.registry import configure
from mockhome.app import create_app as create_home_app


def _message(
    tool: str,
    args: dict,
    action_class: str,
    *,
    consent: Consent,
    kind: str = "inferred",
) -> PlanProposed:
    now = datetime(2026, 10, 18, 22, 41, 5, tzinfo=timezone.utc)
    return PlanProposed(
        plan=Plan(
            plan_id="pln_" + tool[:8],
            created_at=now,
            expires_at=now + PLAN_TTL,
            trigger=Trigger(kind=kind, room=args.get("room")),  # type: ignore[arg-type]
            action_class=action_class,  # type: ignore[arg-type]
            intent=f"test {tool}",
            actions=[Action(action_id="act_1", tool=tool, args=args)],
            confidence=0.9,
            consent=consent,
            reasoning_summary="test",
        )
    )


def test_stub_rules_reject_comfort_without_consent_and_approve_the_rest() -> None:
    now = datetime(2026, 10, 18, 22, 41, tzinfo=timezone.utc)
    comfort = _message(
        "dim_lights",
        {"room": "living_room", "luminosity": 30},
        "comfort",
        consent=Consent(required=True),
    ).plan
    assert decide(comfort) == ("rejected", "comfort plan requires consent")
    consented = comfort.model_copy(
        update={"consent": Consent(required=True, given_by="typing", at=now, question="Dim the lights?")}
    )
    assert decide(consented)[0] == "approved"
    notify = _message(
        "show_caption",
        {"text": "Doorbell.", "size": "large", "priority": "normal"},
        "notify",
        consent=Consent(required=False, given_by="profile", at=now),
        kind="sound",
    ).plan
    safety = _message(
        "alert_caregiver",
        {"severity": "high", "message": "no response"},
        "safety",
        consent=Consent(required=False),
        kind="safety",
    ).plan
    assert decide(notify)[0] == "approved"
    assert decide(safety)[0] == "approved"


class _FakeSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def accept(self) -> None:
        return None

    async def send_json(self, message: dict) -> None:
        self.sent.append(message)

    async def close(self) -> None:
        return None


async def test_queued_plan_is_sent_after_hello_and_dropped_once_expired() -> None:
    clock = FrozenClock()
    link = ExecutorLink(configure(load_home()), clock)
    waiting = _message(
        "announce",
        {"text": "Alex has been notified", "room": "living_room"},
        "notify",
        consent=Consent(required=False, given_by="profile", at=clock.now()),
        kind="sound",
    )
    waiting = waiting.model_copy(
        update={
            "plan": waiting.plan.model_copy(
                update={
                    "plan_id": "pln_queued0001",
                    "created_at": clock.now(),
                    "expires_at": clock.now() + PLAN_TTL,
                }
            )
        }
    )
    await link.submit(waiting)
    socket = _FakeSocket()
    assert await link.attach_executor(socket) is True
    await link.on_executor_message({"type": "hello", "name": "executor_stub", "version": "1.0"})
    assert [item["plan"]["plan_id"] for item in socket.sent if item.get("type") == "plan.proposed"] == [
        "pln_queued0001"
    ]
    clock.advance(31)
    await link.sweep()
    expired = link.records["pln_queued0001"]
    assert expired.decision is not None
    assert expired.decision.reason == "expired"
    await link.detach_executor(socket)
    late = _FakeSocket()
    assert await link.attach_executor(late) is True
    await link.on_executor_message({"type": "hello", "name": "executor_stub", "version": "1.0"})
    assert [item for item in late.sent if item.get("type") == "plan.proposed"] == []


async def test_plan_expires_and_ui_hears_executor_offline() -> None:
    clock = FrozenClock()
    link = ExecutorLink(configure(load_home()), clock)
    message = _message(
        "dim_lights",
        {"room": "living_room", "luminosity": 30},
        "comfort",
        consent=Consent(required=True, given_by="typing", at=clock.now()),
    )
    message = message.model_copy(
        update={
            "plan": message.plan.model_copy(
                update={"plan_id": "pln_expire0001", "created_at": clock.now(), "expires_at": clock.now() + PLAN_TTL}
            )
        }
    )
    record = await link.submit(message)
    assert record.decision is None
    assert any(item.get("status") == OFFLINE for item in link.recent)
    clock.advance(30)
    await link.sweep()
    assert record.decision is not None
    assert record.decision.decision == "rejected"
    assert record.decision.reason == "expired"
    assert record.finished.is_set()


def _free_port() -> int:
    with socket.socket() as opened:
        opened.bind(("127.0.0.1", 0))
        return int(opened.getsockname()[1])


async def _serve(app: object, port: int) -> tuple[uvicorn.Server, asyncio.Task[None]]:
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started:
            return server, task
        await asyncio.sleep(0.02)
    raise RuntimeError(f"server did not start on {port}")


async def _collect(url: str, sink: list[dict], stop: asyncio.Event) -> None:
    async with websockets.connect(url) as socket:
        while not stop.is_set():
            try:
                raw = await asyncio.wait_for(socket.recv(), timeout=0.1)
            except TimeoutError:
                continue
            sink.append(json.loads(raw))


@pytest.fixture
async def running_stack():
    home_port = _free_port()
    harness_port = _free_port()
    home = load_home()
    home_app = create_home_app(home)
    settings = Settings(port=harness_port, home_url=f"http://127.0.0.1:{home_port}", config_path=home.path)
    harness_app = create_app(settings, home=home)
    home_server, home_task = await _serve(home_app, home_port)
    harness_server, harness_task = await _serve(harness_app, harness_port)
    stop = asyncio.Event()
    stub = asyncio.create_task(
        run_stub(
            f"ws://127.0.0.1:{harness_port}/ws/executor",
            f"http://127.0.0.1:{home_port}",
            stop,
            home=home,
        )
    )
    async with httpx.AsyncClient() as client:
        for _ in range(50):
            try:
                response = await client.get(f"http://127.0.0.1:{harness_port}/api/health")
            except httpx.HTTPError:
                await asyncio.sleep(0.05)
                continue
            if response.status_code == 200 and response.json()["executor_connected"]:
                break
            await asyncio.sleep(0.05)
        else:
            raise RuntimeError("executor stub did not connect")
    ui: list[dict] = []
    caregiver: list[dict] = []
    watch = asyncio.Event()
    ui_task = asyncio.create_task(_collect(f"ws://127.0.0.1:{harness_port}/ws/ui", ui, watch))
    caregiver_task = asyncio.create_task(
        _collect(f"ws://127.0.0.1:{harness_port}/ws/caregiver", caregiver, watch)
    )
    await asyncio.sleep(0.1)
    try:
        yield {
            "home": f"http://127.0.0.1:{home_port}",
            "harness": f"http://127.0.0.1:{harness_port}",
            "ui": ui,
            "caregiver": caregiver,
        }
    finally:
        watch.set()
        stop.set()
        home_server.should_exit = True
        harness_server.should_exit = True
        stub.cancel()
        ui_task.cancel()
        caregiver_task.cancel()
        await asyncio.gather(home_task, harness_task, stub, ui_task, caregiver_task, return_exceptions=True)


async def _wait_until(items: list[dict], predicate, timeout: float = 5) -> None:
    started = asyncio.get_running_loop().time()
    while asyncio.get_running_loop().time() - started < timeout:
        if predicate(items):
            return
        await asyncio.sleep(0.02)
    raise AssertionError(items)


async def test_live_stub_executes_consent_and_forwards_alerts(running_stack: dict) -> None:
    harness = running_stack["harness"]
    home = running_stack["home"]
    async with httpx.AsyncClient(timeout=10) as client:
        consented = await client.post(
            f"{harness}/api/plans",
            json=_wire(
                "pln_dim0000001",
                "dim_lights",
                {"room": "living_room", "luminosity": 30, "light": "lamp.living_floor"},
                "comfort",
                consent={"required": True, "given_by": "typing", "at": "2026-10-18T22:41:05Z", "question": "Dim the floor lamp?"},
            ),
        )
        assert consented.status_code == 200, consented.text
        body = consented.json()
        assert body["decision"]["decision"] == "approved"
        assert body["result"]["status"] == "succeeded"
        result = body["result"]["action_results"][0]
        assert result["previous_state"]["lights"]["lamp.living_floor"]["luminosity"] == 100
        assert result["new_state"]["lights"]["lamp.living_floor"]["luminosity"] == 30

        rejected = await client.post(
            f"{harness}/api/plans",
            json=_wire(
                "pln_dim0000002",
                "dim_lights",
                {"room": "living_room", "luminosity": 5, "light": "lamp.living_floor"},
                "comfort",
                consent={"required": True},
            ),
        )
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["decision"]["decision"] == "rejected"
        assert "consent" in rejected.json()["decision"]["reason"]
        state = (await client.get(f"{home}/api/state")).json()
        assert state["rooms"]["living_room"]["lights"]["lamp.living_floor"]["luminosity"] == 30

        notify = await client.post(
            f"{harness}/api/plans",
            json=_wire(
                "pln_cap0000001",
                "show_caption",
                {"text": "Doorbell. Someone is at the front door.", "size": "xl", "priority": "normal"},
                "notify",
                kind="sound",
                consent={"required": False, "given_by": "profile", "at": "2026-10-18T22:41:06Z"},
            ),
        )
        assert notify.status_code == 200, notify.text
        assert notify.json()["decision"]["decision"] == "approved"
        caption = (await client.get(f"{home}/api/state")).json()["rooms"]["living_room"]["caption"]
        assert caption["text"].startswith("Doorbell")

        safety = await client.post(
            f"{harness}/api/plans",
            json=_wire(
                "pln_alt0000001",
                "alert_caregiver",
                {"severity": "high", "message": "Sam may have fallen in the living room"},
                "safety",
                kind="safety",
                consent={"required": False},
            ),
        )
        assert safety.status_code == 200, safety.text
        assert safety.json()["decision"]["decision"] == "approved"
        alert = safety.json()["result"]["action_results"][0]["new_state"]["alert"]
        assert alert["acknowledged"] is False
        assert alert["snapshot"] is None
        ack = await client.post(f"{home}/api/caregiver/ack", json={"alert_id": alert["alert_id"]})
        assert ack.status_code == 200, ack.text
        assert ack.json()["acknowledged"] is True

    await _wait_until(
        running_stack["ui"],
        lambda items: _plan_types(items, "pln_dim0000001") == ["plan.proposed", "plan.decision", "plan.result"],
    )
    await _wait_until(
        running_stack["caregiver"],
        lambda items: any(item.get("type") == "caregiver.alert" and item.get("plan_id") == "pln_alt0000001" for item in items),
    )


def _plan_types(items: list[dict], plan_id: str) -> list[str]:
    found = []
    for item in items:
        if item.get("type") in {"plan.proposed", "plan.decision", "plan.result"}:
            owner = item.get("plan_id") or (item.get("plan") or {}).get("plan_id")
            if owner == plan_id:
                found.append(item["type"])
    return found


def _wire(plan_id: str, tool: str, args: dict, action_class: str, *, consent: dict, kind: str = "inferred") -> dict:
    return {
        "type": "plan.proposed",
        "schema_version": "1.0",
        "plan": {
            "plan_id": plan_id,
            "created_at": "2026-10-18T22:41:05Z",
            "expires_at": "2026-10-18T22:41:35Z",
            "trigger": {"kind": kind, "room": args.get("room")},
            "action_class": action_class,
            "intent": f"test {tool}",
            "actions": [{"action_id": "act_1", "tool": tool, "args": args}],
            "confidence": 0.9,
            "consent": consent,
            "profile": {"needs": ["speech"], "delivered_via": ["caption", "speech"]},
            "reasoning_summary": "test",
            "evidence": [{"kind": "state", "device_id": "lamp.living_floor"}],
        },
    }
