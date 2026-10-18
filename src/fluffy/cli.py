"""Command line for the harness, output service, executor, and schema export."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

from fluffy.config import load_local_env, load_settings, repo_root
from fluffy.models.contracts import (
    ActionResult,
    AuthorizationRecord,
    HomeState,
    OutputMessage,
    PlanProposed,
    PlanSubmission,
    UiEvent,
)
from fluffy.models.scene import scene_schema
from fluffy.tools.registry import build_registry


def export_schemas(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    settings = load_settings()
    registry = build_registry(settings.home)
    schemas = {
        "plan.schema.json": PlanProposed.model_json_schema(),
        "plan_submission.schema.json": PlanSubmission.model_json_schema(),
        "authorization.schema.json": AuthorizationRecord.model_json_schema(),
        "action_result.schema.json": ActionResult.model_json_schema(),
        "home_state.schema.json": HomeState.model_json_schema(),
        "output_message.schema.json": OutputMessage.model_json_schema(),
        "ui_event.schema.json": UiEvent.model_json_schema(),
        "tools.schema.json": registry.manifest({name: True for name in registry.names()}),
        "scene.schema.json": scene_schema(),
    }
    for name, schema in schemas.items():
        (directory / name).write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")


def _call(tool: str, args: list[str]) -> int:
    import httpx

    settings = load_settings()
    payload: dict = {}
    for item in args:
        if "=" not in item:
            raise SystemExit(f"expected key=value, got {item}")
        key, value = item.split("=", 1)
        if value.isdigit():
            payload[key] = int(value)
        else:
            payload[key] = value
    response = httpx.post(
        f"{settings.harness_url}/api/dev/request",
        json={"tool": tool, "args": payload, "intent": "Developer explicit request"},
        timeout=30,
    )
    print(response.text)
    return 0 if response.status_code < 400 else 1


def child_environment() -> dict[str, str]:
    """Environment inherited by the harness, output service, and executor."""

    load_local_env()
    env = os.environ.copy()
    if not env.get("FLUFFY_INTERNAL_TOKEN"):
        env["FLUFFY_INTERNAL_TOKEN"] = secrets.token_urlsafe(24)
    return env


def _dev(with_frontend: bool) -> int:
    root = repo_root()
    env = child_environment()
    commands = [
        [sys.executable, "-m", "mockhome.app"],
        [sys.executable, "-m", "fluffy.server.app"],
        [sys.executable, "-m", "executor_stub.main"],
    ]
    processes = [subprocess.Popen(command, cwd=root, env=env) for command in commands]
    frontend = root / "frontend"
    if with_frontend and (frontend / "package.json").exists():
        npm = "npm"
        processes.append(subprocess.Popen([npm, "run", "dev"], cwd=frontend, env=env))
    print("Harness http://127.0.0.1:8000")
    print("Output service http://127.0.0.1:8100")
    print("Frontend http://127.0.0.1:3000")
    print("Spotify callback http://127.0.0.1:8100/api/integrations/spotify/callback")
    try:
        codes = [process.wait() for process in processes]
    except KeyboardInterrupt:
        for process in processes:
            process.terminate()
        return 0
    return max(codes)


def main() -> None:
    load_local_env()
    parser = argparse.ArgumentParser(prog="fluffy")
    sub = parser.add_subparsers(dest="command", required=True)
    dev = sub.add_parser("dev", help="start the output service, harness, and executor")
    dev.add_argument("--no-frontend", action="store_true")
    sub.add_parser("harness")
    sub.add_parser("home")
    sub.add_parser("executor")
    call = sub.add_parser("call", help="submit one labeled developer request")
    call.add_argument("tool")
    call.add_argument("args", nargs="*")
    schemas = sub.add_parser("schemas")
    schemas.add_argument("--out", default="schemas")
    args = parser.parse_args()
    if args.command == "schemas":
        export_schemas(Path(args.out))
        return
    if args.command == "call":
        raise SystemExit(_call(args.tool, args.args))
    if args.command == "dev":
        raise SystemExit(_dev(not args.no_frontend))
    if args.command == "harness":
        from fluffy.server.app import main as run

        run()
    elif args.command == "home":
        from mockhome.app import main as run

        run()
    elif args.command == "executor":
        from executor_stub.main import main as run

        run()


if __name__ == "__main__":
    main()
