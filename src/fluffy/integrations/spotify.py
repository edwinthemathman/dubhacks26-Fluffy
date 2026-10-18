"""Spotify Authorization Code flow and Web API playback.

Tokens stay on the server. The adapter never switches devices by itself.
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from fluffy.config import IntegrationsConfig

SCOPES = "user-modify-playback-state user-read-playback-state"
AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"


class SpotifyError(Exception):
    def __init__(self, error_code: str, message: str) -> None:
        self.error_code = error_code
        self.message = message
        super().__init__(message)


@dataclass
class SpotifyDevice:
    id: str
    name: str
    is_active: bool
    supports_volume: bool


@dataclass
class PlaybackView:
    device_id: str | None
    track_uri: str | None
    is_playing: bool
    volume_percent: int | None
    supports_volume: bool


@dataclass
class SpotifyOutcome:
    status: str
    verification: str
    error_code: str | None = None
    detail: str = ""
    provider_reference: str | None = None
    playback: PlaybackView | None = None


@dataclass
class SpotifyState:
    refresh_token: str = ""
    access_token: str = ""
    access_expires_at: datetime | None = None
    selected_device_id: str = ""
    selected_device_name: str = ""
    supports_volume: bool = False
    verified_once: bool = False


class SpotifyAdapter:
    def __init__(
        self,
        config: IntegrationsConfig,
        token_path: Path,
        client: httpx.Client | None = None,
        poll_interval_s: float = 1.0,
    ) -> None:
        self.config = config
        self.token_path = token_path
        self.poll_interval_s = poll_interval_s
        self._client = client or httpx.Client(timeout=10)
        self.state = SpotifyState()
        self._oauth_states: dict[str, datetime] = {}
        self._load()

    def _load(self) -> None:
        if not self.token_path.exists():
            return
        raw = json.loads(self.token_path.read_text(encoding="utf-8"))
        expires = raw.get("access_expires_at")
        self.state = SpotifyState(
            refresh_token=raw.get("refresh_token", ""),
            access_token=raw.get("access_token", ""),
            access_expires_at=datetime.fromisoformat(expires) if expires else None,
            selected_device_id=raw.get("selected_device_id", ""),
            selected_device_name=raw.get("selected_device_name", ""),
            supports_volume=bool(raw.get("supports_volume", False)),
            verified_once=bool(raw.get("verified_once", False)),
        )

    def _save(self) -> None:
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "refresh_token": self.state.refresh_token,
            "access_token": self.state.access_token,
            "access_expires_at": self.state.access_expires_at.isoformat() if self.state.access_expires_at else None,
            "selected_device_id": self.state.selected_device_id,
            "selected_device_name": self.state.selected_device_name,
            "supports_volume": self.state.supports_volume,
            "verified_once": self.state.verified_once,
        }
        self.token_path.write_text(json.dumps(payload), encoding="utf-8")

    def readiness(self) -> tuple[str, str]:
        if not self.config.spotify_configured:
            return "unavailable", "Spotify client id and secret are not set"
        if not self.state.refresh_token:
            return "needs_authorization", "Connect Spotify to grant playback permission"
        if not self.state.selected_device_id:
            return "configured", "Authorized. Select the demo device before playback."
        if self.state.verified_once:
            return "ready", f"Device {self.state.selected_device_name or self.state.selected_device_id} selected"
        return "configured", "Device selected. Playback has not been verified yet."

    def begin_oauth(self, now: datetime) -> str:
        if not self.config.spotify_configured:
            raise SpotifyError("spotify_not_configured", "Spotify client credentials are not set")
        state = secrets.token_urlsafe(24)
        self._oauth_states[state] = now + timedelta(minutes=10)
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.config.spotify_client_id,
                "scope": SCOPES,
                "redirect_uri": self.config.spotify_redirect_uri,
                "state": state,
            }
        )
        return f"{AUTH_URL}?{query}"

    def exchange_code(self, code: str, state: str, now: datetime) -> None:
        expires = self._oauth_states.pop(state, None)
        if expires is None or expires < now:
            raise SpotifyError("spotify_needs_authorization", "OAuth state is missing or expired")
        response = self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.config.spotify_redirect_uri,
            }
        )
        self._store_token_response(response, now)

    def list_devices(self) -> list[SpotifyDevice]:
        payload = self._api("GET", "/me/player/devices")
        devices = []
        for item in payload.get("devices", []):
            devices.append(
                SpotifyDevice(
                    id=item.get("id") or "",
                    name=item.get("name") or "Spotify device",
                    is_active=bool(item.get("is_active")),
                    supports_volume=bool(item.get("supports_volume", False)),
                )
            )
        return [device for device in devices if device.id]

    def select_device(self, device_id: str) -> SpotifyDevice:
        match = next((device for device in self.list_devices() if device.id == device_id), None)
        if match is None:
            raise SpotifyError("spotify_no_device", "That device is not on the account")
        self.state.selected_device_id = match.id
        self.state.selected_device_name = match.name
        self.state.supports_volume = match.supports_volume
        self._save()
        return match

    def play(self, uri: str) -> SpotifyOutcome:
        device = self._require_device()
        try:
            self._api(
                "PUT",
                "/me/player/play",
                params={"device_id": device},
                json={"uris": [uri]},
            )
        except SpotifyError as exc:
            return SpotifyOutcome("failed", "unverified", exc.error_code, exc.message)
        playback = self._confirm(lambda view: view.is_playing and view.track_uri == uri and view.device_id == device)
        if playback is None:
            return SpotifyOutcome(
                "unknown",
                "unverified",
                "spotify_unverified",
                "Spotify did not confirm the track is playing on the selected device",
            )
        self.state.verified_once = True
        self._save()
        return SpotifyOutcome("succeeded", "playback_confirmed", playback=playback, provider_reference=uri)

    def pause(self) -> SpotifyOutcome:
        device = self._require_device()
        try:
            self._api("PUT", "/me/player/pause", params={"device_id": device})
        except SpotifyError as exc:
            if exc.error_code == "spotify_connection_error":
                return SpotifyOutcome("unknown", "unverified", exc.error_code, exc.message)
            return SpotifyOutcome("failed", "unverified", exc.error_code, exc.message)
        playback = self._confirm(lambda view: view.device_id == device and not view.is_playing)
        if playback is None:
            return SpotifyOutcome("unknown", "unverified", "spotify_unverified", "Pause was not confirmed")
        return SpotifyOutcome("succeeded", "playback_confirmed", playback=playback)

    def set_volume(self, volume: int) -> SpotifyOutcome:
        device = self._require_device()
        if not self.state.supports_volume:
            return SpotifyOutcome("failed", "unverified", "volume_unsupported", "Selected device does not support volume")
        try:
            self._api(
                "PUT",
                "/me/player/volume",
                params={"volume_percent": volume, "device_id": device},
            )
        except SpotifyError as exc:
            return SpotifyOutcome("failed", "unverified", exc.error_code, exc.message)
        playback = self._confirm(lambda view: view.volume_percent == volume and view.device_id == device)
        if playback is None:
            return SpotifyOutcome("unknown", "unverified", "spotify_unverified", "Volume was not confirmed")
        return SpotifyOutcome("succeeded", "playback_confirmed", playback=playback)

    def current(self) -> PlaybackView | None:
        try:
            payload = self._api("GET", "/me/player")
        except SpotifyError:
            return None
        if not payload:
            return None
        return _view(payload)

    def _require_device(self) -> str:
        if not self.config.spotify_configured:
            raise SpotifyError("spotify_not_configured", "Spotify is not configured")
        if not self.state.refresh_token:
            raise SpotifyError("spotify_needs_authorization", "Spotify is not authorized")
        if not self.state.selected_device_id:
            raise SpotifyError("spotify_no_device", "No Spotify device is selected")
        return self.state.selected_device_id

    def _confirm(self, predicate, attempts: int = 3) -> PlaybackView | None:
        for index in range(attempts):
            view = self.current()
            if view and predicate(view):
                return view
            if index < attempts - 1 and self.poll_interval_s:
                time.sleep(self.poll_interval_s)
        return None

    def _api(self, method: str, path: str, params: dict | None = None, json: dict | None = None, retried: bool = False) -> dict:
        token = self._access_token()
        try:
            response = self._client.request(
                method,
                f"{API}{path}",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                json=json,
            )
        except httpx.TimeoutException as exc:
            raise SpotifyError("spotify_connection_error", "Spotify request timed out") from exc
        except httpx.HTTPError as exc:
            raise SpotifyError("spotify_connection_error", "Spotify connection failed") from exc
        if response.status_code == 401 and not retried:
            self._refresh(datetime.now(timezone.utc))
            return self._api(method, path, params, json, retried=True)
        if response.status_code == 429:
            raise SpotifyError("spotify_rate_limited", "Spotify rate limit")
        if response.status_code == 404:
            raise SpotifyError("spotify_no_device", "Spotify device or player is unavailable")
        if response.status_code == 403:
            raise SpotifyError("spotify_missing_scope", "Spotify token is missing a playback scope")
        if response.status_code >= 400:
            code = "spotify_track_unavailable" if response.status_code in {400, 502} else "spotify_connection_error"
            raise SpotifyError(code, f"Spotify request failed ({response.status_code})")
        if not response.content:
            return {}
        return response.json()

    def _access_token(self) -> str:
        now = datetime.now(timezone.utc)
        if self.state.access_token and self.state.access_expires_at and self.state.access_expires_at > now + timedelta(seconds=15):
            return self.state.access_token
        self._refresh(now)
        return self.state.access_token

    def _refresh(self, now: datetime) -> None:
        if not self.state.refresh_token:
            raise SpotifyError("spotify_needs_authorization", "Spotify refresh token is missing")
        response = self._token_request(
            {"grant_type": "refresh_token", "refresh_token": self.state.refresh_token}
        )
        self._store_token_response(response, now)

    def _token_request(self, data: dict[str, str]) -> dict[str, Any]:
        try:
            response = self._client.post(
                TOKEN_URL,
                data=data,
                auth=(self.config.spotify_client_id, self.config.spotify_client_secret),
            )
        except httpx.HTTPError as exc:
            raise SpotifyError("spotify_connection_error", "Spotify token request failed") from exc
        if response.status_code >= 400:
            raise SpotifyError("spotify_needs_authorization", "Spotify rejected the token request")
        return response.json()

    def _store_token_response(self, payload: dict[str, Any], now: datetime) -> None:
        self.state.access_token = payload.get("access_token", "")
        if payload.get("refresh_token"):
            self.state.refresh_token = payload["refresh_token"]
        expires_in = int(payload.get("expires_in", 3600))
        self.state.access_expires_at = now + timedelta(seconds=expires_in)
        self._save()


def _view(payload: dict) -> PlaybackView:
    device = payload.get("device") or {}
    item = payload.get("item") or {}
    return PlaybackView(
        device_id=device.get("id"),
        track_uri=item.get("uri"),
        is_playing=bool(payload.get("is_playing")),
        volume_percent=device.get("volume_percent"),
        supports_volume=bool(device.get("supports_volume", False)),
    )


# Kept so tests can build an in-memory token file without touching the network.
def blank_state() -> SpotifyState:
    return SpotifyState()


_ = field

from fluffy.integrations.spotify_clap import (  # noqa: E402
    CLAP_ALBUM,
    CLAP_ARTIST,
    CLAP_TITLE,
    CLAP_TRACK,
    pause_playback,
    play_clap_song,
    set_volume,
)
