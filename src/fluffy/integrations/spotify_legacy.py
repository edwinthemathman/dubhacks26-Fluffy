"""Spotify playback for play_music and stop_music."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx

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
        if client_id is None:
            from fluffy.config import load_integrations

            client_id = load_integrations().spotify_client_id
        self.client_id = client_id.strip()
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

    def now_playing(self) -> dict[str, Any]:
        token = self._token()
        if token is None:
            return {"ok": False, "playing": False}
        found = self._request("GET", f"{API}/me/player", token)
        if "error" in found:
            return {"ok": False, "playing": False}
        body = found.get("body") or {}
        device = body.get("device") or {}
        return {"ok": True, "playing": bool(body.get("is_playing")), "volume": device.get("volume_percent")}

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
