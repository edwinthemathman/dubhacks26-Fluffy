"""Harness to executor to output service, then a fresh read of the same state."""

import asyncio
import socket
import threading
import time

import httpx
import uvicorn

from fluffy.config import load_settings
from fluffy.server.app import create_harness_app
from executor_stub.main import run_executor
from mockhome.app import create_home_app


def _output_state(settings) -> dict:
    response = httpx.get(
        f"{settings.home_url}/api/state",
        headers={"Authorization": f"Bearer {settings.internal_token}"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _serve(app, port: int) -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    server.install_signal_handlers = False
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("server did not start")
        time.sleep(0.05)
    return server


def test_developer_dim_updates_authoritative_state(tmp_path):
    settings = load_settings()
    settings.home_port = _port()
    settings.harness_port = _port()
    settings.data_dir = tmp_path
    settings.internal_token = "test-internal-token"
    settings.autotick_safety = False
    home = _serve(create_home_app(settings), settings.home_port)
    harness = _serve(create_harness_app(settings), settings.harness_port)

    def _executor() -> None:
        asyncio.run(run_executor(settings))

    thread = threading.Thread(target=_executor, daemon=True)
    thread.start()
    client = httpx.Client(base_url=settings.harness_url, timeout=10)
    try:
        deadline = time.time() + 10
        while time.time() < deadline:
            health = client.get("/api/health")
            if health.status_code == 200 and health.json().get("executor_online"):
                break
            time.sleep(0.05)
        else:
            raise AssertionError("executor did not connect")

        rejected = client.post(
            "/api/dev/request",
            json={"tool": "dim_lights", "args": {"room": "living_room", "luminosity": 10}, "confidence": 1},
        )
        assert rejected.status_code == 422
        bad_args = client.post(
            "/api/dev/request",
            json={"tool": "dim_lights", "args": {"room": "living_room", "luminosity": 10, "confidence": 1}},
        )
        assert bad_args.status_code == 200
        assert bad_args.json()["error_code"] == "invalid_arguments"

        response = client.post(
            "/api/dev/request",
            json={
                "tool": "dim_lights",
                "args": {"room": "living_room", "luminosity": 35, "light": "lamp.living_floor"},
                "intent": "Developer explicit request",
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "submitted"
        assert body["schema_version"] == "2.0"

        deadline = time.time() + 10
        snapshot = None
        refreshed = None
        while time.time() < deadline:
            snapshot = _output_state(settings)
            floor = next(light for light in snapshot["lights"] if light["id"] == "lamp.living_floor")
            if floor["luminosity"] != 35:
                time.sleep(0.05)
                continue
            time.sleep(0.05)
            refreshed = _output_state(settings)
            if refreshed["revision"] == snapshot["revision"]:
                break
        else:
            raise AssertionError(f"brightness did not settle: {snapshot}")

        assert refreshed is not None
        floor = next(light for light in refreshed["lights"] if light["id"] == "lamp.living_floor")
        assert floor["luminosity"] == 35
        assert refreshed["revision"] == snapshot["revision"]
        live = client.get("/api/state").json()
        assert live["type"] == "state"
        assert "lamp" in live["state"]

        class _Dim:
            async def interpret(self, text: str, window=None):
                del text, window
                raise AssertionError("typed text goes to the live loop")

        # The product route calls the injected interpreter. This double stands in for Perceptron.
        settings_text = load_settings()
        settings_text.home_port = _port()
        settings_text.harness_port = _port()
        settings_text.data_dir = tmp_path / "text"
        settings_text.internal_token = "test-internal-token"
        settings_text.autotick_safety = False
        home_text = _serve(create_home_app(settings_text), settings_text.home_port)
        harness_text = _serve(create_harness_app(settings_text, interpreter=_Dim()), settings_text.harness_port)

        def _text_executor() -> None:
            asyncio.run(run_executor(settings_text))

        threading.Thread(target=_text_executor, daemon=True).start()
        text_client = httpx.Client(base_url=settings_text.harness_url, timeout=10)
        try:
            deadline = time.time() + 10
            while time.time() < deadline:
                health = text_client.get("/api/health")
                if health.status_code == 200 and health.json().get("executor_online"):
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("executor did not connect for text")
            accepted = text_client.post(
                "/api/input/text",
                json={"session_id": settings_text.session_id, "request_id": "req_dim30", "text": "dim the floor lamp to 30"},
            )
            assert accepted.status_code == 202, accepted.text
            assert accepted.json()["completed"] is False
            live = text_client.get("/api/state").json()
            assert live["type"] == "state"
        finally:
            text_client.close()
            harness_text.should_exit = True
            home_text.should_exit = True
    finally:
        client.close()
        harness.should_exit = True
        home.should_exit = True
