"""Provider failures stay unverified. Fakes never replace a missing live setup."""

import json
from datetime import datetime, timezone

import httpx

from fluffy.config import IntegrationsConfig
from fluffy.integrations.elevenlabs import ElevenLabsAdapter
from fluffy.integrations.photon import PhotonAdapter
from fluffy.integrations.spotify import SpotifyAdapter


NOW = datetime(2026, 10, 18, 1, 0, tzinfo=timezone.utc)


def test_readiness_is_unavailable_without_credentials(tmp_path):
    config = IntegrationsConfig()
    speech = ElevenLabsAdapter(config, httpx.Client())
    photon = PhotonAdapter(config, httpx.Client())
    spotify = SpotifyAdapter(config, tmp_path / "tokens.json", httpx.Client(), poll_interval_s=0)
    assert speech.readiness_detail()[0] == "unavailable"
    assert photon.readiness()[0] == "unavailable"
    assert spotify.readiness()[0] == "unavailable"


def test_spotify_partial_volume_and_unverified_playback(tmp_path):
    calls = {"play": 0, "volume": 0, "player": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/token"):
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        if request.url.path.endswith("/me/player/play"):
            calls["play"] += 1
            return httpx.Response(204)
        if request.url.path.endswith("/me/player/volume"):
            calls["volume"] += 1
            return httpx.Response(403, json={"error": {"reason": "VOLUME_CONTROL_DISALLOW"}})
        if request.url.path.endswith("/me/player"):
            calls["player"] += 1
            if calls["player"] == 1:
                return httpx.Response(
                    200,
                    json={
                        "is_playing": True,
                        "item": {"uri": "spotify:track:abc"},
                        "device": {"id": "laptop", "volume_percent": 20, "supports_volume": False},
                    },
                )
            return httpx.Response(204)
        return httpx.Response(500, json={})

    config = IntegrationsConfig(spotify_client_id="client", spotify_client_secret="secret")
    adapter = SpotifyAdapter(config, tmp_path / "tokens.json", httpx.Client(transport=httpx.MockTransport(handler)), poll_interval_s=0)
    adapter.state.refresh_token = "refresh"
    adapter.state.access_token = "token"
    adapter.state.access_expires_at = datetime(2026, 10, 18, 2, 0, tzinfo=timezone.utc)
    adapter.state.selected_device_id = "laptop"
    adapter.state.supports_volume = False
    played = adapter.play("spotify:track:abc")
    volume = adapter.set_volume(35)
    assert played.status == "succeeded"
    assert played.verification == "playback_confirmed"
    assert volume.status == "failed"
    assert volume.error_code == "volume_unsupported"
    assert calls["play"] == 1


def test_spotify_unverified_when_playback_does_not_match(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/me/player/play"):
            return httpx.Response(204)
        if request.url.path.endswith("/me/player"):
            return httpx.Response(200, json={"is_playing": False, "item": None, "device": {"id": "other"}})
        return httpx.Response(204)

    config = IntegrationsConfig(spotify_client_id="client", spotify_client_secret="secret")
    adapter = SpotifyAdapter(config, tmp_path / "tokens.json", httpx.Client(transport=httpx.MockTransport(handler)), poll_interval_s=0)
    adapter.state.refresh_token = "refresh"
    adapter.state.access_token = "token"
    adapter.state.access_expires_at = datetime(2026, 10, 18, 2, 0, tzinfo=timezone.utc)
    adapter.state.selected_device_id = "laptop"
    outcome = adapter.play("spotify:track:abc")
    assert outcome.status == "unknown"
    assert outcome.verification == "unverified"
    assert outcome.error_code == "spotify_unverified"


def test_photon_statuses_stay_distinct():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            assert request.headers["idempotency-key"]
            body = json.loads(request.content)
            assert body["spaceId"] == "pho_spc_configured"
            assert "phone" not in body
            return httpx.Response(202, json={"id": "pho_msg_1", "status": "dispatched"})
        return httpx.Response(200, json={"id": "pho_msg_1", "status": "delivered"})

    config = IntegrationsConfig(
        photon_api_key="key",
        photon_project_id="pho_prj_1",
        photon_sender_id="pho_usr_1",
        caregiver_destination="pho_spc_configured",
    )
    adapter = PhotonAdapter(config, httpx.Client(transport=httpx.MockTransport(handler)))
    accepted = adapter.send_alert("inc_1", "pho_spc_configured", "Please check on them.", "pln:act")
    assert accepted.acceptance_status == "accepted"
    assert accepted.verification == "provider_accepted"
    delivered = adapter.get_delivery("pho_msg_1")
    assert delivered.acceptance_status == "delivered"
    assert delivered.verification == "provider_delivered"

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    slow = PhotonAdapter(config, httpx.Client(transport=httpx.MockTransport(timeout)))
    unknown = slow.send_alert("inc_1", "pho_spc_configured", "Please check on them.", "pln:act")
    assert unknown.acceptance_status == "unknown"
    assert unknown.verification == "unverified"


def test_elevenlabs_failure_is_not_audio(tmp_path):
    del tmp_path
    config = IntegrationsConfig(elevenlabs_api_key="key", elevenlabs_voice_id="voice", elevenlabs_model_id="model")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/text-to-speech/voice/stream"
        assert request.headers["xi-api-key"] == "key"
        return httpx.Response(500, json={"detail": "no"})

    adapter = ElevenLabsAdapter(config, httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        adapter.synthesize("Hello", 2)
        raise AssertionError("synthesis should fail")
    except Exception as exc:
        assert exc.error_code == "synthesis_failed"
