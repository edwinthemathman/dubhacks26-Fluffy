"""Spotify playback for plans, and the one-shot clap song.

A play request that names only the track URI returns success and then leaves
the player empty. Starting the track's album and seeking to that track is
what actually starts audio.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[3]
TOKEN_PATH = ROOT / ".spotify_token"
API = "https://api.spotify.com/v1"
ACCOUNTS = "https://accounts.spotify.com/api/token"


class Spotify:
    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        token_path: Path | None = None,
        client_id: str | None = None,
    ) -> None:
        self.client = client or httpx.Client()
        self.token_path = token_path or TOKEN_PATH
        self.client_id = (client_id if client_id is not None else os.environ.get("SPOTIFY_CLIENT_ID", "")).strip()
        self._access = ""
        self._access_until = 0.0

    def play(self, query: str | None = None, volume: int | None = None) -> dict[str, Any]:
        token = self._token()
        if token is None:
            return {"ok": False, "error": "not logged in"}
        body: dict[str, Any] | None = None
        track = None
        if query and str(query).strip():
            found = self._search(token, str(query).strip())
            if "error" in found:
                return found
            track = found
            body = {"uris": [track["uri"]]}
        device = self._device(token)
        if "error" in device:
            return device
        played = self._request(
            "PUT",
            f"{API}/me/player/play",
            token,
            params={"device_id": device["id"]},
            json=body,
        )
        if "error" in played:
            return played
        if volume is not None:
            self._request("PUT", f"{API}/me/player/volume", token, params={"volume_percent": int(volume)})
        result: dict[str, Any] = {"ok": True}
        if track:
            result["track"] = track["name"]
            result["artist"] = track["artist"]
        return result

    def stop(self) -> dict[str, Any]:
        token = self._token()
        if token is None:
            return {"ok": False, "error": "not logged in"}
        paused = self._request("PUT", f"{API}/me/player/pause", token)
        if "error" in paused:
            return paused
        return {"ok": True}

    def _search(self, token: str, query: str) -> dict[str, Any]:
        found = self._request("GET", f"{API}/search", token, params={"type": "track", "limit": 1, "q": query})
        if "error" in found:
            return found
        items = ((found.get("body") or {}).get("tracks") or {}).get("items") or []
        if not items:
            return {"ok": False, "error": "no matching song"}
        item = items[0]
        artists = item.get("artists") or [{}]
        return {"uri": item.get("uri"), "name": item.get("name") or query, "artist": artists[0].get("name") or ""}

    def _device(self, token: str) -> dict[str, Any]:
        listed = self._request("GET", f"{API}/me/player/devices", token)
        if "error" in listed:
            return listed
        devices = (listed.get("body") or {}).get("devices") or []
        if not devices:
            return {"ok": False, "error": "no active Spotify device, open Spotify"}
        chosen = next((item for item in devices if item.get("is_active")), devices[0])
        if not chosen.get("id"):
            return {"ok": False, "error": "no active Spotify device, open Spotify"}
        return {"id": chosen["id"]}

    def _token(self) -> str | None:
        now = time.time()
        if self._access and now < self._access_until - 30:
            return self._access
        if not self.token_path.is_file():
            return None
        try:
            saved = json.loads(self.token_path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(saved, dict):
            return None
        if saved.get("access_token") and float(saved.get("expires_at") or 0) > now + 30:
            self._access = str(saved["access_token"])
            self._access_until = float(saved["expires_at"])
            return self._access
        refresh = str(saved.get("refresh_token") or "")
        if not refresh or not self.client_id:
            return None
        response = self.client.post(
            ACCOUNTS,
            data={"grant_type": "refresh_token", "refresh_token": refresh, "client_id": self.client_id},
            timeout=15,
        )
        if response.status_code >= 400:
            return None
        payload = response.json()
        self._access = str(payload.get("access_token") or "")
        self._access_until = now + float(payload.get("expires_in") or 3600)
        return self._access or None

    def _request(self, method: str, url: str, token: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self.client.request(
                method,
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
                **kwargs,
            )
        except httpx.HTTPError:
            return {"ok": False, "error": "not logged in"}
        if response.status_code in {401, 403}:
            if response.status_code == 401 and "premium" not in response.text.lower():
                return {"ok": False, "error": "not logged in"}
            return {"ok": False, "error": "Spotify Premium required"}
        if response.status_code >= 400:
            return {"ok": False, "error": "no active Spotify device, open Spotify"}
        if not response.content:
            return {"ok": True, "body": {}}
        try:
            body = response.json()
        except json.JSONDecodeError:
            body = {}
        return {"ok": True, "body": body if isinstance(body, dict) else {}}


# Should I Stay or Should I Go - Remastered, The Clash, on the album that
# starts playback on this account.
CLAP_TRACK = "spotify:track:6SjbzGo7arjfM5cXpXhc5B"
CLAP_ALBUM = "spotify:album:0CTCk1eshEadFqZ4NBfe9N"
CLAP_TITLE = "Should I Stay or Should I Go - Remastered"
CLAP_ARTIST = "The Clash"


def play_clap_song(client: httpx.Client | None = None) -> dict[str, Any]:
    """Start the clap song. `ok` is true only after the player reports it playing."""
    own = client is None
    if own:
        load_dotenv(ROOT / ".env")
        client = httpx.Client(timeout=15)
    assert client is not None
    try:
        token = _load_token()
        if token is None:
            return _miss("not logged in")
        client_id = os.environ.get("SPOTIFY_CLIENT_ID", "")
        token = _refresh(client, token, client_id)
        headers = {"Authorization": f"Bearer {token['access_token']}"}
        devices = client.get(f"{API}/me/player/devices", headers=headers)
        if devices.status_code == 401:
            return _miss("not logged in")
        if devices.status_code != 200:
            return _miss(f"could not list devices ({devices.status_code})")
        device = _pick_device(devices.json().get("devices") or [])
        if device is None:
            return _miss("no active Spotify device, open Spotify")
        already = _now_playing(client, headers)
        if already is not None:
            _repeat_off(client, headers)
            return already
        body = {"context_uri": CLAP_ALBUM, "offset": {"uri": CLAP_TRACK}, "position_ms": 0}
        play = client.put(
            f"{API}/me/player/play",
            params={"device_id": device["id"]},
            headers=headers,
            json=body,
        )
        if play.status_code not in (202, 204):
            return _miss(_spotify_error(play))
        check = client.get(f"{API}/me/player", headers=headers)
        if check.status_code != 200:
            return _miss("Spotify did not start the song")
        player = check.json()
        item = player.get("item") or {}
        name = item.get("name") or ""
        if not player.get("is_playing") or "Should I Stay" not in name:
            return _miss("Spotify did not start the song")
        _repeat_off(client, headers)
        artists = ", ".join(a.get("name", "") for a in item.get("artists") or []) or CLAP_ARTIST
        device_info = player.get("device") or {}
        return {
            "ok": True,
            "track": name,
            "artist": artists,
            "device": device.get("name") or "",
            "volume": device_info.get("volume_percent"),
            "error": "",
        }
    except httpx.HTTPError as exc:
        return _miss(str(exc))
    finally:
        if own:
            client.close()


def _now_playing(client: httpx.Client, headers: dict[str, str]) -> dict[str, Any] | None:
    """The clap song if it is already playing. A second play would start it over."""
    check = client.get(f"{API}/me/player", headers=headers)
    if check.status_code != 200 or not check.content:
        return None
    player = check.json()
    item = player.get("item") or {}
    name = item.get("name") or ""
    if not player.get("is_playing") or "Should I Stay" not in name:
        return None
    artists = ", ".join(a.get("name", "") for a in item.get("artists") or []) or CLAP_ARTIST
    device_info = player.get("device") or {}
    return {
        "ok": True,
        "track": name,
        "artist": artists,
        "device": device_info.get("name") or "",
        "volume": device_info.get("volume_percent"),
        "error": "",
    }


def _repeat_off(client: httpx.Client, headers: dict[str, str]) -> None:
    """One play-through. Spotify must not loop the song or its album."""
    try:
        client.put(f"{API}/me/player/repeat", params={"state": "off"}, headers=headers)
    except httpx.HTTPError:
        return


def pause_playback(client: httpx.Client | None = None) -> dict[str, Any]:
    """Pause whatever is playing on the open Spotify device."""
    own = client is None
    if own:
        load_dotenv(ROOT / ".env")
        client = httpx.Client(timeout=15)
    assert client is not None
    try:
        headers, error = _headers(client)
        if headers is None:
            return {"ok": False, "error": error}
        response = client.put(f"{API}/me/player/pause", headers=headers)
        if response.status_code == 401:
            return {"ok": False, "error": "not logged in"}
        if response.status_code not in (202, 204):
            return {"ok": False, "error": _spotify_error(response)}
        return {"ok": True, "error": ""}
    except httpx.HTTPError as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        if own:
            client.close()


def set_volume(percent: int, client: httpx.Client | None = None) -> dict[str, Any]:
    """Set the open Spotify device's volume. 0 is mute, 100 is full."""
    level = max(0, min(100, int(percent)))
    own = client is None
    if own:
        load_dotenv(ROOT / ".env")
        client = httpx.Client(timeout=15)
    assert client is not None
    try:
        headers, error = _headers(client)
        if headers is None:
            return {"ok": False, "volume": level, "error": error}
        response = client.put(
            f"{API}/me/player/volume",
            params={"volume_percent": level},
            headers=headers,
        )
        if response.status_code == 401:
            return {"ok": False, "volume": level, "error": "not logged in"}
        if response.status_code not in (202, 204):
            return {"ok": False, "volume": level, "error": _spotify_error(response)}
        return {"ok": True, "volume": level, "error": ""}
    except httpx.HTTPError as exc:
        return {"ok": False, "volume": level, "error": str(exc)}
    finally:
        if own:
            client.close()


def _headers(client: httpx.Client) -> tuple[dict[str, str] | None, str]:
    token = _load_token()
    if token is None:
        return None, "not logged in"
    token = _refresh(client, token, os.environ.get("SPOTIFY_CLIENT_ID", ""))
    return {"Authorization": f"Bearer {token['access_token']}"}, ""


def _miss(error: str) -> dict[str, Any]:
    return {"ok": False, "track": CLAP_TITLE, "artist": CLAP_ARTIST, "device": "", "error": error}


def _load_token() -> dict[str, Any] | None:
    if not TOKEN_PATH.exists():
        return None
    data = json.loads(TOKEN_PATH.read_text())
    if not data.get("access_token") or not data.get("refresh_token"):
        return None
    return data


def _refresh(client: httpx.Client, token: dict[str, Any], client_id: str) -> dict[str, Any]:
    if not client_id:
        return token
    response = client.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": token["refresh_token"],
            "client_id": client_id,
        },
    )
    if response.status_code != 200:
        return token
    fresh = response.json()
    token = {
        **token,
        "access_token": fresh["access_token"],
        "expires_in": fresh.get("expires_in", token.get("expires_in")),
        "refresh_token": fresh.get("refresh_token", token["refresh_token"]),
    }
    TOKEN_PATH.write_text(json.dumps(token))
    return token


def _pick_device(devices: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not devices:
        return None
    for device in devices:
        if device.get("is_active"):
            return device
    for device in devices:
        if "macbook" in (device.get("name") or "").lower():
            return device
    return devices[0]


def _spotify_error(response: httpx.Response) -> str:
    if response.status_code == 404:
        return "no active Spotify device, open Spotify"
    if response.status_code == 403:
        return "Spotify Premium required"
    try:
        message = response.json().get("error", {}).get("message")
    except ValueError:
        message = None
    return message or f"Spotify play failed ({response.status_code})"
