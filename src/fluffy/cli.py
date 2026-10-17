"""Command line for the execution subsystem.

    fluffy dev
    fluffy home
    fluffy harness
    fluffy executor-stub
    fluffy schemas
    fluffy call dim_lights room=living_room luminosity=30 --consent=typing
"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
import uuid
from datetime import timedelta
from typing import Any, Literal

import httpx
import uvicorn
from pydantic import ValidationError

from fluffy.clock import SystemClock
from fluffy.config import ConfigError, HomeConfig, load_home
from fluffy.export import export_schemas
from fluffy.models.plan import (
    PLAN_TTL,
    Action,
    Consent,
    Plan,
    PlanProposed,
    PlanValidationError,
    Trigger,
    normalize_plan,
)
from fluffy.tools.registry import BoundTools, ToolArgumentError, configure

ConsentBy = Literal["nod", "voice", "button", "typing", "spoken_request", "profile"]
TriggerKind = Literal["spoken", "inferred", "sound", "safety"]


def main(argv: list[str] | None = None) -> None:
    try:
        code = _main(argv)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        code = 1
    raise SystemExit(code)


def _main(argv: list[str] | None) -> int:
    parser = argparse.ArgumentParser(prog="fluffy")
    sub = parser.add_subparsers(dest="command", required=True)

    dev = sub.add_parser("dev", help="run the mock home, harness, and stub executor")
    _add_bind(dev, 8000)
    dev.add_argument("--home-port", type=int, default=8100)

    home = sub.add_parser("home", help="run the mock home on port 8100")
    _add_bind(home, 8100)

    harness = sub.add_parser("harness", help="run the harness on port 8000")
    _add_bind(harness, 8000)
    harness.add_argument("--home-url", default="http://127.0.0.1:8100")

    stub = sub.add_parser("executor-stub", help="run the fixed-rule executor")
    stub.add_argument("--harness-url", default="ws://127.0.0.1:8000/ws/executor")
    stub.add_argument("--home-url", default="http://127.0.0.1:8100")

    schemas = sub.add_parser("schemas", help="export plan, tool, and scene JSON Schemas")
    schemas.add_argument("--out", default="schemas")

    call = sub.add_parser("call", help="send one plan through the harness and stub")
    call.add_argument("tool")
    call.add_argument("assignments", nargs="*", metavar="name=value")
    call.add_argument("--consent", choices=["nod", "voice", "button", "typing", "spoken_request", "profile"])
    call.add_argument("--question", default=None)
    call.add_argument("--intent", default=None)
    call.add_argument("--trigger", choices=["spoken", "inferred", "sound", "safety"])
    call.add_argument("--utterance", default=None)
    call.add_argument("--event", default=None)
    call.add_argument("--confidence", type=float, default=1.0)
    call.add_argument("--harness-url", default="http://127.0.0.1:8000")

    for item in (dev, home, harness, stub, schemas, call):
        item.add_argument("--config", default=None, help="path to home.toml")

    args = parser.parse_args(argv)
    config = load_home(None if args.config is None else _path(args.config))
    if args.command == "schemas":
        written = export_schemas(_path(args.out), config)
        for path in written:
            print(path)
        return 0
    if args.command == "home":
        _serve_home(config, args.host, args.port)
        return 0
    if args.command == "harness":
        _serve_harness(config, args.host, args.port, args.home_url)
        return 0
    if args.command == "executor-stub":
        _serve_stub(config, _ws_url(args.harness_url), args.home_url)
        return 0
    if args.command == "dev":
        asyncio.run(_serve_dev(config, args.host, args.port, args.home_port))
        return 0
    if args.command == "call":
        return _call(config, args)
    return 2


def _add_bind(parser: argparse.ArgumentParser, port: int) -> None:
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=port)


def _path(value: str) -> Any:
    from pathlib import Path

    return Path(value)


def _serve_home(home: HomeConfig, host: str, port: int) -> None:
    from mockhome.app import create_app

    uvicorn.run(create_app(home), host=host, port=port, log_level="info")


def _serve_harness(home: HomeConfig, host: str, port: int, home_url: str) -> None:
    from fluffy.server.app import Settings, create_app

    settings = Settings(host=host, port=port, home_url=home_url, config_path=home.path)
    uvicorn.run(create_app(settings, home=home), host=host, port=port, log_level="info")


def _serve_stub(home: HomeConfig, harness_ws: str, home_url: str) -> None:
    from executor_stub.main import run_stub

    asyncio.run(run_stub(harness_ws, home_url, home=home))


async def _serve_dev(home: HomeConfig, host: str, harness_port: int, home_port: int) -> None:
    from executor_stub.main import run_stub
    from fluffy.server.app import Settings, create_app
    from mockhome.app import create_app as create_home_app

    home_url = f"http://{host}:{home_port}"
    harness_app = create_app(
        Settings(host=host, port=harness_port, home_url=home_url, config_path=home.path),
        home=home,
    )
    home_server = uvicorn.Server(uvicorn.Config(create_home_app(home), host=host, port=home_port, log_level="info"))
    harness_server = uvicorn.Server(uvicorn.Config(harness_app, host=host, port=harness_port, log_level="info"))
    home_server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    harness_server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _request_stop, home_server, harness_server, stop)
    print(f"mock home   {home_url}")
    print(f"harness     http://{host}:{harness_port}")
    print(f"executor    ws://{host}:{harness_port}/ws/executor")
    print(f"ui          ws://{host}:{harness_port}/ws/ui")
    print(f"caregiver   ws://{host}:{harness_port}/ws/caregiver")
    print("call        fluffy call dim_lights room=living_room luminosity=30 --consent=typing")
    stub = asyncio.create_task(
        run_stub(f"ws://{host}:{harness_port}/ws/executor", home_url, stop, home=home)
    )
    try:
        await asyncio.gather(home_server.serve(), harness_server.serve(), stub)
    finally:
        stop.set()
        home_server.should_exit = True
        harness_server.should_exit = True
        stub.cancel()


def _request_stop(home_server: uvicorn.Server, harness_server: uvicorn.Server, stop: asyncio.Event) -> None:
    home_server.should_exit = True
    harness_server.should_exit = True
    stop.set()


def _call(home: HomeConfig, args: argparse.Namespace) -> int:
    try:
        assignments = _assignments(args.assignments)
        message = build_plan(
            configure(home),
            args.tool,
            assignments,
            consent=args.consent,
            question=args.question,
            intent=args.intent,
            trigger=args.trigger,
            utterance=args.utterance,
            event=args.event,
            confidence=args.confidence,
        )
    except (ToolArgumentError, PlanValidationError, ValidationError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    url = args.harness_url.rstrip("/") + "/api/plans"
    try:
        response = httpx.post(url, json=message.model_dump(mode="json"), params={"wait": "true"}, timeout=40.0)
    except httpx.HTTPError as exc:
        print(f"harness unreachable at {args.harness_url}: {exc}", file=sys.stderr)
        return 1
    if response.status_code >= 400:
        print(response.text, file=sys.stderr)
        return 1
    body = response.json()
    print(json.dumps(body, indent=2))
    decision = (body.get("decision") or {}).get("decision")
    result = (body.get("result") or {}).get("status")
    if decision == "approved" and result == "succeeded":
        return 0
    return 1


def build_plan(
    bound: BoundTools,
    tool_name: str,
    args: dict[str, Any],
    *,
    consent: ConsentBy | None,
    question: str | None,
    intent: str | None,
    trigger: TriggerKind | None,
    utterance: str | None,
    event: str | None,
    confidence: float,
) -> PlanProposed:
    action_class = bound.action_class(tool_name)
    normalized = bound.parse_plan_arguments(tool_name, args)
    kind = trigger or _default_trigger(action_class, consent)
    now = SystemClock().now()
    if consent is None and action_class == "comfort":
        consent_model = Consent(required=True, question=question)
    elif consent is None:
        consent_model = Consent(required=False, given_by="profile", at=now, question=question)
    else:
        consent_model = Consent(
            required=action_class == "comfort",
            given_by=consent,
            at=now,
            question=question,
        )
    room = normalized.get("room")
    plan = Plan(
        plan_id="pln_" + uuid.uuid4().hex[:12],
        created_at=now,
        expires_at=now + timedelta(seconds=PLAN_TTL.total_seconds()),
        trigger=Trigger(
            kind=kind,
            utterance=utterance,
            event=event,
            room=room if isinstance(room, str) else None,
        ),
        action_class=action_class,
        intent=intent or _intent(tool_name, normalized),
        actions=[Action(action_id="act_1", tool=tool_name, args=normalized)],
        confidence=confidence,
        consent=consent_model,
        reasoning_summary="manual fluffy call",
    )
    return normalize_plan(PlanProposed(plan=plan), bound)


def _default_trigger(action_class: str, consent: str | None) -> TriggerKind:
    if consent == "spoken_request":
        return "spoken"
    if action_class == "safety":
        return "safety"
    if action_class == "notify":
        return "sound"
    return "inferred"


def _intent(tool_name: str, args: dict[str, Any]) -> str:
    details = ", ".join(f"{key}={value}" for key, value in args.items())
    text = tool_name.replace("_", " ")
    return f"{text} ({details})" if details else text


def _assignments(items: list[str]) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"expected name=value, got {item}")
        key, raw = item.split("=", 1)
        if not key:
            raise ValueError(f"expected name=value, got {item}")
        parsed[key] = _coerce(raw)
    return parsed


def _coerce(value: str) -> Any:
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if value.isdigit() or (value.startswith("-") and value[1:].isdigit()):
        return int(value)
    if any(mark in value for mark in ".eE"):
        try:
            return float(value)
        except ValueError:
            return value
    return value


def _ws_url(value: str) -> str:
    if value.startswith("http://"):
        return "ws://" + value.removeprefix("http://").rstrip("/") + "/ws/executor"
    if value.startswith("https://"):
        return "wss://" + value.removeprefix("https://").rstrip("/") + "/ws/executor"
    return value
