"""The clap song has to start from its album. A bare track URI does not play."""

import json

import httpx

from fluffy.integrations.spotify import CLAP_ALBUM, CLAP_TRACK, play_clap_song, set_volume


def test_clap_song_starts_from_the_album(tmp_path, monkeypatch):
    token = tmp_path / "token.json"
    token.write_text(json.dumps({"access_token": "a", "refresh_token": "r"}))
    monkeypatch.setattr("fluffy.integrations.spotify.TOKEN_PATH", token)
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "")
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/devices"):
            return httpx.Response(
                200,
                json={"devices": [{"id": "mac", "name": "Kelton's MacBook Pro", "is_active": True}]},
            )
        if request.url.path.endswith("/repeat"):
            seen["repeat"] = request.url.params["state"]
            return httpx.Response(204)
        if request.url.path.endswith("/play"):
            seen["plays"] = seen.get("plays", 0) + 1
            seen["body"] = json.loads(request.content)
            seen["device"] = request.url.params["device_id"]
            return httpx.Response(204)
        if request.url.path.endswith("/player"):
            seen["looks"] = seen.get("looks", 0) + 1
            if seen["looks"] == 1:
                return httpx.Response(204)
            return httpx.Response(
                200,
                json={
                    "is_playing": True,
                    "device": {"name": "Kelton's MacBook Pro", "volume_percent": 40},
                    "item": {
                        "name": "Should I Stay or Should I Go - Remastered",
                        "artists": [{"name": "The Clash"}],
                    },
                },
            )
        return httpx.Response(500)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = play_clap_song(client)
        again = play_clap_song(client)

    assert result["ok"] is True
    assert again["ok"] is True
    assert result["device"] == "Kelton's MacBook Pro"
    assert seen["plays"] == 1
    assert seen["repeat"] == "off"
    assert seen["device"] == "mac"
    assert seen["body"]["context_uri"] == CLAP_ALBUM
    assert seen["body"]["offset"]["uri"] == CLAP_TRACK
    assert "uris" not in seen["body"]


def test_volume_sets_the_open_player(tmp_path, monkeypatch):
    token = tmp_path / "token.json"
    token.write_text(json.dumps({"access_token": "a", "refresh_token": "r"}))
    monkeypatch.setattr("fluffy.integrations.spotify.TOKEN_PATH", token)
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "")
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["percent"] = request.url.params["volume_percent"]
        return httpx.Response(204)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = set_volume(42, client)

    assert result == {"ok": True, "volume": 42, "error": ""}
    assert seen["percent"] == "42"
