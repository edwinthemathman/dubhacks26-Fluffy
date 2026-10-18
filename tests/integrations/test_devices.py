"""New device tools: validation, consent, and mocked Spotify and Photon."""

import json
from pathlib import Path

import httpx
import pytest

from fluffy.brain.consent import ConsentManager
from fluffy.clock import FrozenClock
from fluffy.ids import IdFactory
from fluffy.integrations import run_tool
from fluffy.integrations.spotify_legacy import Spotify
from fluffy.models import Action, Consent, Trigger, build_plan
from fluffy.models.tool_catalog import ToolError, parse_call
from fluffy.notify import photon
from tests.inference.helpers import profiles


def test_new_tools_validate():
    opened = parse_call(
        "open_door",
        json.dumps({"door": "front_door", "confidence": 0.9, "reason": "asked to open"}),
    )
    assert opened.args == {"door": "front_door"}
    sent = parse_call(
        "send_text",
        json.dumps({"to": "alex", "message": "on my way", "confidence": 0.9, "reason": "asked"}),
    )
    assert sent.args["message"] == "on my way"
    stopped = parse_call("stop_music", json.dumps({"confidence": 0.8, "reason": "asked"}))
    assert stopped.args == {}
    with pytest.raises(ToolError):
        parse_call(
            "send_text",
            json.dumps({"to": "alex", "message": "x" * 301, "confidence": 0.9, "reason": "too long"}),
        )


def test_spoken_send_text_still_asks():
    clock = FrozenClock()
    plan = build_plan(
        clock=clock,
        ids=IdFactory(),
        trigger=Trigger(kind="spoken", utterance="text Alex that I am home", room="living_room"),
        action_class="comfort",
        intent="Text Alex",
        actions=[Action(action_id="act_text", tool="send_text", args={"to": "alex", "message": "I am home"})],
        confidence=0.95,
        consent=Consent(required=True),
        needs=["speech"],
        delivered_via=["speech"],
        reasoning_summary="they asked",
    )
    update = ConsentManager(clock).consider(plan, profiles()["blind"], transcript_clear=True)
    assert update.status == "asking"
    assert update.plan is not None
    assert update.plan.consent.given_by is None


def test_text_tools_say_unavailable_without_photon(monkeypatch):
    monkeypatch.delenv("PHOTON_API_KEY", raising=False)
    monkeypatch.delenv("PHOTON_PROJECT_ID", raising=False)
    monkeypatch.delenv("PHOTON_FROM", raising=False)
    monkeypatch.delenv("PHOTON_CAREGIVER_HANDLE", raising=False)
    photon._shared = None
    assert run_tool("send_text", {"to": "alex", "message": "hi"})["error"] == "texting unavailable"
    assert run_tool("alert_caregiver", {"severity": "high", "message": "Possible fall"})["error"] == "texting unavailable"


def test_call_person_is_labeled_a_demo():
    result = run_tool("call_person", {"to": "alex"})
    assert result["ok"] is True
    assert result["label"] == "call (demo)"


class _Transport(httpx.MockTransport):
    pass


def test_spotify_play_prefers_the_active_device(tmp_path: Path):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"tracks": {"items": [{"uri": "spotify:track:1", "name": "Blue", "artists": [{"name": "Ada"}]}]}})
        if request.url.path.endswith("/devices"):
            return httpx.Response(200, json={"devices": [{"id": "laptop", "is_active": False}, {"id": "phone", "is_active": True}]})
        if request.url.path.endswith("/play"):
            return httpx.Response(204)
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    spotify = Spotify(client=client, token_path=tmp_path / "missing")
    spotify._access = "token"
    spotify._access_until = 10**12
    result = spotify.play("blue")
    assert result == {"ok": True, "track": "Blue", "artist": "Ada"}
    play = next(item for item in seen if item.url.path.endswith("/play"))
    assert play.url.params["device_id"] == "phone"
    assert json.loads(play.content) == {"uris": ["spotify:track:1"]}


def test_spotify_errors_are_plain_words(tmp_path: Path):
    def empty(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search"):
            return httpx.Response(
                200,
                json={"tracks": {"items": [{"uri": "spotify:track:1", "name": "Jazz", "artists": [{"name": "Ada"}]}]}},
            )
        return httpx.Response(200, json={"devices": []})

    def premium(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="premium required")

    missing = Spotify(client=httpx.Client(transport=httpx.MockTransport(empty)), token_path=tmp_path / "nope")
    missing._access = "token"
    missing._access_until = 10**12
    assert missing.play("jazz")["error"] == "no active Spotify device, open Spotify"

    blocked = Spotify(client=httpx.Client(transport=httpx.MockTransport(premium)), token_path=tmp_path / "nope")
    blocked._access = "token"
    blocked._access_until = 10**12
    assert blocked.stop()["error"] == "Spotify Premium required"

    logged_out = Spotify(token_path=Path("/tmp/fluffy-no-spotify-token"))
    assert logged_out.play("jazz")["error"] == "not logged in"
