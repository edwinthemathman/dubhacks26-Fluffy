"""Environment loading stays on the backend and does not echo secrets."""

import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from fluffy.cli import child_environment
from fluffy.config import ensure_env_file, load_local_env, load_settings, repo_root
from fluffy.live.text_bridge import UnavailableInterpreter
from fluffy.server.app import create_harness_app

SECRET = "dummy-provider-secret"
PROBE = "FLUFFY_ENV_PROBE"


def test_env_file_loads_from_repo_root_when_cwd_differs(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    other = tmp_path / "elsewhere"
    root.mkdir()
    other.mkdir()
    (root / ".env").write_text("FLUFFY_SESSION_ID=ses_from_file\n", encoding="utf-8")
    monkeypatch.delenv("FLUFFY_SESSION_ID", raising=False)
    monkeypatch.chdir(other)
    load_local_env(root)
    assert os.environ["FLUFFY_SESSION_ID"] == "ses_from_file"


def test_process_environment_overrides_env_file(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".env").write_text("FLUFFY_SESSION_ID=ses_from_file\n", encoding="utf-8")
    monkeypatch.setenv("FLUFFY_SESSION_ID", "ses_from_process")
    load_local_env(root)
    assert os.environ["FLUFFY_SESSION_ID"] == "ses_from_process"


def test_existing_env_file_is_not_replaced(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".env.example").write_text("PERCEPTRON_API_KEY=\n", encoding="utf-8")
    (root / ".env").write_text("PERCEPTRON_API_KEY=keep-local\n", encoding="utf-8")
    ensure_env_file(root)
    assert "keep-local" in (root / ".env").read_text(encoding="utf-8")


def test_child_processes_inherit_dummy_environment(monkeypatch):
    monkeypatch.setenv(PROBE, "inherited")
    env = child_environment()
    assert env[PROBE] == "inherited"
    completed = subprocess.run(
        [sys.executable, "-c", "import os; print(os.environ.get('FLUFFY_ENV_PROBE', ''))"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.strip() == "inherited"


def test_public_health_hides_unverified_credentials():
    settings = load_settings(
        environ={
            "FLUFFY_INTERNAL_TOKEN": SECRET,
            "PERCEPTRON_API_KEY": SECRET,
            "ELEVENLABS_API_KEY": SECRET,
            "ELEVENLABS_VOICE_ID": "voice",
            "ELEVENLABS_MODEL_ID": "model",
            "SPOTIFY_CLIENT_ID": "client",
            "SPOTIFY_CLIENT_SECRET": SECRET,
            "PHOTON_API_KEY": SECRET,
            "PHOTON_PROJECT_ID": "project",
            "PHOTON_SENDER_ID": "sender",
            "CAREGIVER_DESTINATION": "pho_spc_example",
            "FLUFFY_HOME_PORT": "9",
            "FLUFFY_AUTOTICK": "0",
        }
    )
    app = create_harness_app(settings, interpreter=UnavailableInterpreter())
    with TestClient(app) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["inference"]["readiness"] == "configured"
    assert body["inference"]["detail"]
    assert SECRET not in response.text
    assert "Authorization" not in response.text
    rendered = repr(settings)
    assert SECRET not in rendered
    assert "internal_token" not in rendered


def test_missing_perceptron_disables_inference_without_claiming_success():
    settings = load_settings(environ={"FLUFFY_HOME_PORT": "9", "FLUFFY_AUTOTICK": "0"})
    app = create_harness_app(settings, interpreter=UnavailableInterpreter())
    with TestClient(app) as client:
        response = client.get("/api/health")
    body = response.json()
    assert body["inference"]["readiness"] == "unavailable"
    assert "not set" in body["inference"]["detail"]
    assert "succeeded" not in body["inference"]["detail"]


def test_git_ignores_secret_files_and_tracks_examples():
    root = repo_root()
    ignored = subprocess.run(
        ["git", "check-ignore", ".env", "frontend/.env.local", "data/spotify_oauth.json", ".spotify_token"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    assert ".env" in ignored.stdout
    assert "spotify_oauth.json" in ignored.stdout
    examples = subprocess.run(
        ["git", "check-ignore", ".env.example", "frontend/.env.example"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert examples.returncode == 1
    tracked = subprocess.run(["git", "ls-files", ".env", "data/spotify_oauth.json", ".spotify_token"], cwd=root, check=True, capture_output=True, text=True)
    assert tracked.stdout.strip() == ""


def test_frontend_source_has_no_provider_secrets():
    banned = (
        "PERCEPTRON_API_KEY",
        "ELEVENLABS_API_KEY",
        "SPOTIFY_CLIENT_SECRET",
        "PHOTON_API_KEY",
        "FLUFFY_INTERNAL_TOKEN",
        "NEXT_PUBLIC_PERCEPTRON",
        "NEXT_PUBLIC_ELEVEN",
        "NEXT_PUBLIC_SPOTIFY",
        "NEXT_PUBLIC_PHOTON",
    )
    root = repo_root() / "frontend"
    offenders: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or any(part in {".next", "node_modules"} for part in path.parts):
            continue
        if path.suffix.lower() not in {".ts", ".tsx", ".js", ".mjs"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(name in text for name in banned):
            offenders.append(str(path.relative_to(root)))
    assert offenders == []
