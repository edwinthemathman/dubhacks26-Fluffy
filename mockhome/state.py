"""Authoritative simulated home. Network calls happen outside the state lock."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta
from pathlib import Path

from fluffy.config import HomeConfig, IntegrationsConfig
from fluffy.ids import new_id
from fluffy.integrations.elevenlabs import ElevenLabsAdapter, SpeechError
from fluffy.integrations.photon import PhotonAdapter
from fluffy.integrations.spotify import SpotifyAdapter, SpotifyError, SpotifyOutcome
from fluffy.models.contracts import (
    AlertView,
    BlindState,
    Caption,
    FlashOverlay,
    HomeState,
    IncidentView,
    LightState,
    MediaState,
    OutputMessage,
    SubResult,
    ToolEffect,
)
from fluffy.tools.registry import Registry, ToolError, canonical_args


FLASH_COLORS = {"blue": "#4C7DFF", "amber": "#F0A202", "white": "#F7F4EA"}


class HomeStore:
    def __init__(
        self,
        home: HomeConfig,
        registry: Registry,
        spotify: SpotifyAdapter,
        speech: ElevenLabsAdapter,
        photon: PhotonAdapter,
        asset_root: Path,
        now,
    ) -> None:
        self.home = home
        self.registry = registry
        self.spotify = spotify
        self.speech = speech
        self.photon = photon
        self.asset_root = asset_root
        self.now = now
        self.lock = threading.Lock()
        self.revision = 0
        self.lights = {
            light.id: LightState(
                id=light.id,
                room=room.id,
                label=light.label,
                luminosity=80,
                warmth="neutral",
                color=home.warmth_colors["neutral"],
            )
            for room in home.rooms
            for light in room.lights
        }
        self.blinds = {
            blind.id: BlindState(id=blind.id, room=room.id, label=blind.label, position=100)
            for room in home.rooms
            for blind in room.blinds
        }
        self.media = {
            room.id: MediaState(room=room.id, volume=35, simulated=True)
            for room in home.rooms
        }
        self.flashes: list[FlashOverlay] = []
        self.captions: list[Caption] = []
        self.alerts: list[AlertView] = []
        self.incident: IncidentView | None = None
        self.audio: dict[str, tuple[bytes, str]] = {}
        self.messages: dict[str, OutputMessage] = {}
        self._waiters: dict[str, threading.Event] = {}
        self._listeners: list = []
        self._cooldown_until: dict[str, datetime] = {}

    def subscribe(self, callback) -> None:
        self._listeners.append(callback)

    def snapshot(self) -> HomeState:
        with self.lock:
            if self._expire_locked():
                self.revision += 1
            return self._snapshot_locked()

    def apply(self, tool: str, args: dict, session_id: str, plan_id: str, action_id: str) -> ToolEffect:
        try:
            model = self.registry.validate_args(tool, args)
        except ToolError as exc:
            kind = "simulated_state"
            try:
                kind = self.registry.get(tool).effect_kind
            except ToolError:
                pass
            return ToolEffect(
                status="failed",
                effect_kind=kind,  # type: ignore[arg-type]
                verification="unverified",
                error_code=exc.error_code,
                detail=exc.message,
            )
        payload = canonical_args(model)
        handler = getattr(self, f"_tool_{tool}")
        return handler(payload, session_id, plan_id, action_id)

    def playback_ack(self, message_id: str, status: str) -> OutputMessage | None:
        if status not in {"started", "ended", "failed", "blocked"}:
            return None
        with self.lock:
            message = self.messages.get(message_id)
            if message is None:
                return None
            message.playback = status
            self.messages[message_id] = message
            for caption in self.captions:
                if caption.message_id == message_id:
                    caption.playback = status
                    caption.audio_url = message.audio_url
            self._touch_locked()
            waiter = self._waiters.get(message_id)
        if waiter and status in {"ended", "failed", "blocked"}:
            waiter.set()
        self._publish()
        return message

    def cancel_local_audio(self) -> None:
        with self.lock:
            pending = [
                message_id
                for message_id, message in self.messages.items()
                if message.playback in {"generated", "started", "none"} and message.speak
            ]
            for message_id in pending:
                self.messages[message_id].playback = "failed"
            waiters = [self._waiters.get(message_id) for message_id in pending]
        for waiter in waiters:
            if waiter is not None:
                waiter.set()

    def dismiss_caption(self, caption_id: str) -> bool:
        with self.lock:
            for caption in self.captions:
                if caption.id == caption_id and not caption.resolved:
                    caption.resolved = True
                    self._touch_locked()
                    self._publish_later = True
                    break
            else:
                return False
        self._publish()
        return True

    def set_incident(self, incident: IncidentView | None) -> None:
        with self.lock:
            self.incident = incident
            self._touch_locked()
        self._publish()

    def audio_bytes(self, audio_id: str) -> tuple[bytes, str] | None:
        return self.audio.get(audio_id)

    def _tool_dim_lights(self, args, session_id, plan_id, action_id) -> ToolEffect:
        del session_id, plan_id, action_id
        room = self.home.room(args["room"])
        assert room is not None
        ids = [args["light"]] if args.get("light") else [light.id for light in room.lights]
        with self.lock:
            previous = {light_id: self.lights[light_id].luminosity for light_id in ids}
            for light_id in ids:
                self.lights[light_id].luminosity = args["luminosity"]
            self._touch_locked()
            new = {light_id: self.lights[light_id].luminosity for light_id in ids}
        self._publish()
        return ToolEffect(
            status="succeeded",
            effect_kind="simulated_state",
            verification="state_committed",
            previous_state={"luminosity": previous},
            new_state={"luminosity": new},
        )

    def _tool_set_light_warmth(self, args, session_id, plan_id, action_id) -> ToolEffect:
        del session_id, plan_id, action_id
        room = self.home.room(args["room"])
        assert room is not None
        ids = [args["light"]] if args.get("light") else [light.id for light in room.lights]
        color = self.home.warmth_colors[args["warmth"]]
        with self.lock:
            previous = {light_id: self.lights[light_id].warmth for light_id in ids}
            for light_id in ids:
                self.lights[light_id].warmth = args["warmth"]
                self.lights[light_id].color = color
            self._touch_locked()
        self._publish()
        return ToolEffect(
            status="succeeded",
            effect_kind="simulated_state",
            verification="state_committed",
            previous_state={"warmth": previous},
            new_state={"warmth": args["warmth"], "color": color},
        )

    def _tool_set_blinds(self, args, session_id, plan_id, action_id) -> ToolEffect:
        del session_id, plan_id, action_id
        room = self.home.room(args["room"])
        assert room is not None
        with self.lock:
            previous = {blind.id: self.blinds[blind.id].position for blind in room.blinds}
            for blind in room.blinds:
                self.blinds[blind.id].position = args["position"]
            self._touch_locked()
        self._publish()
        return ToolEffect(
            status="succeeded",
            effect_kind="simulated_state",
            verification="state_committed",
            previous_state={"position": previous},
            new_state={"position": args["position"]},
        )

    def _tool_flash_lights(self, args, session_id, plan_id, action_id) -> ToolEffect:
        del session_id, plan_id
        room = self.home.room(args["room"])
        assert room is not None
        now = self.now()
        duration = (args["pulses"] * (600 + 600)) / 1000
        overlay = FlashOverlay(
            id=action_id,
            room=args["room"],
            light_ids=[light.id for light in room.lights],
            color=args["color"],
            pulses=args["pulses"],
            started_at=now,
            ends_at=now + timedelta(seconds=duration),
        )
        with self.lock:
            self.flashes = [item for item in self.flashes if item.id != overlay.id]
            self.flashes.append(overlay)
            base = {light.id: self.lights[light.id].luminosity for light in room.lights}
            self._touch_locked()
        self._publish()
        return ToolEffect(
            status="succeeded",
            effect_kind="simulated_state",
            verification="state_committed",
            previous_state={"luminosity": base},
            new_state={"luminosity": base, "flash": overlay.model_dump(mode="json")},
            detail="overlay only; base brightness unchanged",
        )

    def _tool_show_caption(self, args, session_id, plan_id, action_id) -> ToolEffect:
        del plan_id
        message = self._add_caption(
            text=args["text"],
            priority=args["priority"],
            ttl_s=None if args["priority"] == "high" else args.get("ttl_s", 10),
            source="show_caption",
            session_id=session_id,
            message_id=action_id,
            speak=False,
        )
        return ToolEffect(
            status="succeeded",
            effect_kind="simulated_state",
            verification="state_committed",
            new_state={"caption_id": message.message_id},
            message=message,
        )

    def _tool_announce(self, args, session_id, plan_id, action_id) -> ToolEffect:
        del plan_id
        message = self._add_caption(
            text=args["text"],
            priority="normal",
            ttl_s=15,
            source="announce",
            session_id=session_id,
            message_id=action_id,
            speak=True,
        )
        try:
            clip = self.speech.synthesize(args["text"], self.home.generation_timeout_s)
        except SpeechError as exc:
            with self.lock:
                message.playback = "failed"
                self.messages[message.message_id] = message
                self._touch_locked()
            self._publish()
            return ToolEffect(
                status="failed",
                effect_kind="browser_audio",
                verification="unverified",
                error_code=exc.error_code,
                detail=exc.message,
                message=message,
            )
        audio_id = new_id("aud")
        waiter = threading.Event()
        with self.lock:
            self.audio[audio_id] = (clip.audio, clip.content_type)
            message.audio_url = f"/api/audio/{audio_id}"
            message.playback = "generated"
            self.messages[message.message_id] = message
            self._sync_caption_locked(message)
            self._waiters[message.message_id] = waiter
            self._touch_locked()
        self._publish()
        remaining = max(1.0, self.home.speech_total_cap_s - self.home.generation_timeout_s)
        signaled = waiter.wait(timeout=remaining)
        with self.lock:
            playback = self.messages[message.message_id].playback
        if playback == "ended":
            return ToolEffect(
                status="succeeded",
                effect_kind="browser_audio",
                verification="playback_confirmed",
                message=self.messages[message.message_id],
                provider_reference=audio_id,
            )
        if playback in {"failed", "blocked"}:
            code = "playback_blocked" if playback == "blocked" else "playback_failed"
            return ToolEffect(
                status="failed",
                effect_kind="browser_audio",
                verification="unverified",
                error_code=code,
                message=self.messages[message.message_id],
                provider_reference=audio_id,
            )
        status = "unknown" if not signaled else "pending"
        return ToolEffect(
            status=status,
            effect_kind="browser_audio",
            verification="unverified",
            error_code="playback_timeout" if not signaled else None,
            detail="audio was generated; playback was not confirmed",
            message=self.messages[message.message_id],
            provider_reference=audio_id,
        )

    def _tool_play_sound(self, args, session_id, plan_id, action_id) -> ToolEffect:
        media = self.home.media_item(args["sound"])
        assert media is not None
        room_media = self.media[args["room"]]
        if (
            self.home.no_restart_if_same_track
            and room_media.playback == "playing"
            and room_media.sound_id == media.id
        ):
            return ToolEffect(
                status="skipped",
                effect_kind="spotify" if media.provider == "spotify" else "browser_audio",
                verification="playback_confirmed" if room_media.verification == "playback_confirmed" else "unverified",
                error_code="already_playing",
                detail="the same track is already playing",
                new_state=room_media.model_dump(mode="json"),
            )
        if media.provider == "spotify":
            return self._play_spotify(media, args, session_id, plan_id, action_id)
        return self._play_local(media, args, session_id, action_id)

    def _play_spotify(self, media, args, session_id, plan_id, action_id) -> ToolEffect:
        del session_id, plan_id, action_id
        with self.lock:
            previous = self.media[args["room"]].model_dump(mode="json")
            self.media[args["room"]].playback = "requesting"
            self.media[args["room"]].sound_id = media.id
            self.media[args["room"]].title = media.title
            self.media[args["room"]].provider = "spotify"
            self.media[args["room"]].simulated = False
            self._touch_locked()
        self._publish()
        if not self.spotify.config.spotify_configured:
            return self._media_error(args["room"], previous, "spotify_not_configured", "Spotify client credentials are not set")
        if not self.spotify.state.refresh_token:
            return self._media_error(args["room"], previous, "spotify_needs_authorization", "Spotify is not authorized")
        if not media.uri or "REPLACE_WITH_TRACK_ID" in media.uri:
            return self._media_error(args["room"], previous, "spotify_track_unavailable", "arrival track URI is not configured")
        try:
            played = self.spotify.play(media.uri)
        except SpotifyError as exc:
            return self._media_error(args["room"], previous, exc.error_code, exc.message)
        volume = self.spotify.set_volume(args["volume"]) if played.status == "succeeded" else None
        sub = [
            SubResult(
                channel="play",
                status=played.status,  # type: ignore[arg-type]
                verification=played.verification,  # type: ignore[arg-type]
                error_code=played.error_code,
                provider_reference=played.provider_reference,
            )
        ]
        if volume is not None:
            sub.append(
                SubResult(
                    channel="volume",
                    status=volume.status,  # type: ignore[arg-type]
                    verification=volume.verification,  # type: ignore[arg-type]
                    error_code=volume.error_code,
                )
            )
        with self.lock:
            state = self.media[args["room"]]
            if played.status == "succeeded":
                state.playback = "playing"
                state.volume = args["volume"] if volume and volume.status == "succeeded" else state.volume
                state.verification = "playback_confirmed"
                state.error_code = None if volume is None or volume.status == "succeeded" else volume.error_code
            elif played.status == "unknown":
                state.playback = "error"
                state.verification = "unverified"
                state.error_code = played.error_code
            else:
                state.playback = "error"
                state.verification = "unverified"
                state.error_code = played.error_code
            self._touch_locked()
            new_state = state.model_dump(mode="json")
        self._publish()
        if played.status == "succeeded" and (volume is None or volume.status == "succeeded"):
            return ToolEffect(
                status="succeeded",
                effect_kind="spotify",
                verification="playback_confirmed",
                previous_state=previous,
                new_state=new_state,
                provider_reference=media.uri,
                sub_results=sub,
            )
        if played.status == "succeeded":
            return ToolEffect(
                status="failed",
                effect_kind="spotify",
                verification="playback_confirmed",
                previous_state=previous,
                new_state=new_state,
                error_code="partial_effects",
                detail="track started; volume was not confirmed",
                provider_reference=media.uri,
                sub_results=sub,
            )
        status = "unknown" if played.status == "unknown" else "failed"
        return ToolEffect(
            status=status,  # type: ignore[arg-type]
            effect_kind="spotify",
            verification="unverified",
            previous_state=previous,
            new_state=new_state,
            error_code=played.error_code,
            detail=played.detail,
            sub_results=sub,
        )

    def _play_local(self, media, args, session_id, action_id) -> ToolEffect:
        path = self.asset_root / (media.asset or "")
        if not path.exists():
            return self._media_error(args["room"], self.media[args["room"]].model_dump(mode="json"), "invalid_arguments", "local asset missing")
        audio = path.read_bytes()
        audio_id = new_id("aud")
        message = self._add_caption(
            text=media.title,
            priority="normal",
            ttl_s=10,
            source="local_sound",
            session_id=session_id,
            message_id=action_id,
            speak=True,
        )
        waiter = threading.Event()
        with self.lock:
            self.audio[audio_id] = (audio, "audio/wav")
            message.audio_url = f"/api/audio/{audio_id}"
            message.playback = "generated"
            self.messages[message.message_id] = message
            self._sync_caption_locked(message)
            self._waiters[message.message_id] = waiter
            state = self.media[args["room"]]
            previous = state.model_dump(mode="json")
            state.provider = "local"
            state.sound_id = media.id
            state.title = media.title
            state.volume = args["volume"]
            state.playback = "requesting"
            state.simulated = True
            state.verification = "unverified"
            self._touch_locked()
        self._publish()
        waiter.wait(timeout=self.home.local_timeout_s)
        with self.lock:
            playback = self.messages[message.message_id].playback
            state = self.media[args["room"]]
            if playback == "ended":
                state.playback = "paused"
                state.verification = "playback_confirmed"
                state.error_code = None
                status = "succeeded"
                verification = "playback_confirmed"
                error = None
            elif playback in {"failed", "blocked"}:
                state.playback = "error"
                state.error_code = "playback_blocked" if playback == "blocked" else "playback_failed"
                status = "failed"
                verification = "unverified"
                error = state.error_code
            else:
                state.playback = "error"
                state.error_code = "playback_timeout"
                status = "unknown"
                verification = "unverified"
                error = "playback_timeout"
            self._touch_locked()
            new_state = state.model_dump(mode="json")
        self._publish()
        return ToolEffect(
            status=status,  # type: ignore[arg-type]
            effect_kind="browser_audio",
            verification=verification,  # type: ignore[arg-type]
            previous_state=previous,
            new_state=new_state,
            error_code=error,
            provider_reference=audio_id,
            message=self.messages[message.message_id],
            detail="local notification asset",
        )

    def _tool_stop_sound(self, args, session_id, plan_id, action_id) -> ToolEffect:
        del session_id, plan_id, action_id
        room = args["room"]
        with self.lock:
            previous = self.media[room].model_dump(mode="json")
            local_playing = self.media[room].provider == "local" and self.media[room].playback in {"playing", "requesting"}
            spotify_playing = self.media[room].provider == "spotify" and self.media[room].playback in {"playing", "requesting"}
            message_ids = [
                message.message_id
                for message in self.messages.values()
                if message.source == "local_sound" and message.playback in {"generated", "started"}
            ]
        local_outcome = SpotifyOutcome("succeeded", "state_committed", detail="no local sound")
        if local_playing or message_ids:
            for message_id in message_ids:
                self.playback_ack(message_id, "ended")
            with self.lock:
                if self.media[room].provider == "local":
                    self.media[room].playback = "paused"
                    self._touch_locked()
            local_outcome = SpotifyOutcome("succeeded", "state_committed", detail="local sound stopped")
        spotify_outcome = SpotifyOutcome("succeeded", "unverified", detail="spotify idle")
        if spotify_playing or self.media[room].provider == "spotify":
            try:
                spotify_outcome = self.spotify.pause()
            except SpotifyError as exc:
                spotify_outcome = SpotifyOutcome("failed", "unverified", exc.error_code, exc.message)
            with self.lock:
                if spotify_outcome.status == "succeeded":
                    self.media[room].playback = "paused"
                    self.media[room].verification = "playback_confirmed"
                    self.media[room].error_code = None
                else:
                    self.media[room].error_code = spotify_outcome.error_code
                self._touch_locked()
        self._publish()
        sub = [
            SubResult(channel="local", status=_outcome_status(local_outcome), verification="state_committed", error_code=local_outcome.error_code),
            SubResult(
                channel="spotify",
                status=_outcome_status(spotify_outcome),
                verification=spotify_outcome.verification if spotify_outcome.verification != "state_committed" else "unverified",  # type: ignore[arg-type]
                error_code=spotify_outcome.error_code,
            ),
        ]
        failed = [item for item in sub if item.status in {"failed", "unknown"}]
        with self.lock:
            new_state = self.media[room].model_dump(mode="json")
        if not failed:
            return ToolEffect(
                status="succeeded",
                effect_kind="spotify" if spotify_playing else "browser_audio",
                verification="playback_confirmed" if spotify_playing else "state_committed",
                previous_state=previous,
                new_state=new_state,
                sub_results=sub,
            )
        return ToolEffect(
            status="unknown" if any(item.status == "unknown" for item in failed) else "failed",
            effect_kind="spotify",
            verification="unverified",
            previous_state=previous,
            new_state=new_state,
            error_code="partial_effects" if any(item.status == "succeeded" for item in sub) else failed[0].error_code,
            sub_results=sub,
            detail="one playback channel did not stop",
        )

    def _tool_set_volume(self, args, session_id, plan_id, action_id) -> ToolEffect:
        del session_id, plan_id, action_id
        with self.lock:
            previous = {"volume": self.media[args["room"]].volume}
        try:
            outcome = self.spotify.set_volume(args["volume"])
        except SpotifyError as exc:
            return ToolEffect(
                status="failed",
                effect_kind="spotify",
                verification="unverified",
                error_code=exc.error_code,
                detail=exc.message,
                previous_state=previous,
            )
        with self.lock:
            if outcome.status == "succeeded":
                self.media[args["room"]].volume = args["volume"]
                self.media[args["room"]].verification = "playback_confirmed"
                self.media[args["room"]].error_code = None
            else:
                self.media[args["room"]].error_code = outcome.error_code
            self._touch_locked()
            new_state = {"volume": self.media[args["room"]].volume}
        self._publish()
        return ToolEffect(
            status=outcome.status,  # type: ignore[arg-type]
            effect_kind="spotify",
            verification=outcome.verification,  # type: ignore[arg-type]
            previous_state=previous,
            new_state=new_state,
            error_code=outcome.error_code,
            detail=outcome.detail,
        )

    def _tool_alert_caregiver(self, args, session_id, plan_id, action_id) -> ToolEffect:
        del session_id
        effect_key = f"{plan_id}:{action_id}"
        with self.lock:
            self.alerts.append(
                AlertView(
                    incident_id=args["incident_id"],
                    severity=args["severity"],
                    message=args["message"],
                    status="submitting",
                    verification="unverified",
                )
            )
            self._touch_locked()
        self._publish()
        result = self.photon.send_alert(
            args["incident_id"],
            self.photon.config.caregiver_destination,
            args["message"],
            effect_key,
        )
        if result.acceptance_status == "accepted" and result.message_reference:
            looked = self.photon.get_delivery(result.message_reference)
            if looked.acceptance_status in {"delivered", "failed"}:
                result = looked
        with self.lock:
            alert = next(item for item in reversed(self.alerts) if item.incident_id == args["incident_id"])
            alert.status = result.acceptance_status  # type: ignore[assignment]
            alert.verification = result.verification  # type: ignore[assignment]
            alert.provider_reference = result.message_reference
            alert.error_code = result.error_code
            self._touch_locked()
        self._publish()
        status = {
            "accepted": "succeeded",
            "delivered": "succeeded",
            "failed": "failed",
            "unknown": "unknown",
        }[result.acceptance_status]
        return ToolEffect(
            status=status,  # type: ignore[arg-type]
            effect_kind="photon",
            verification=result.verification,  # type: ignore[arg-type]
            provider_reference=result.message_reference,
            error_code=result.error_code,
            detail=result.detail,
            new_state={"alert_status": result.acceptance_status, "incident_id": args["incident_id"]},
        )

    def _add_caption(self, text, priority, ttl_s, source, session_id, message_id, speak) -> OutputMessage:
        now = self.now()
        expires = None if ttl_s is None else now + timedelta(seconds=ttl_s)
        caption = Caption(
            id=message_id,
            text=text,
            priority=priority,
            created_at=now,
            expires_at=expires,
            source=source,
            message_id=message_id,
            speak=speak,
        )
        message = OutputMessage(
            message_id=message_id,
            text=text,
            priority=priority,
            created_at=now,
            expires_at=expires,
            speak=speak,
            playback="none",
            source=source,
            session_id=session_id,
        )
        with self.lock:
            self.captions.append(caption)
            self.messages[message_id] = message
            self._touch_locked()
        self._publish()
        return message

    def _media_error(self, room: str, previous: dict, error_code: str, detail: str) -> ToolEffect:
        with self.lock:
            self.media[room].playback = "error"
            self.media[room].error_code = error_code
            self.media[room].verification = "unverified"
            self._touch_locked()
            new_state = self.media[room].model_dump(mode="json")
        self._publish()
        kind = "spotify" if self.media[room].provider == "spotify" else "browser_audio"
        return ToolEffect(
            status="failed",
            effect_kind=kind,  # type: ignore[arg-type]
            verification="unverified",
            previous_state=previous,
            new_state=new_state,
            error_code=error_code,
            detail=detail,
        )

    def _sync_caption_locked(self, message: OutputMessage) -> None:
        for caption in self.captions:
            if caption.message_id == message.message_id:
                caption.speak = message.speak
                caption.audio_url = message.audio_url
                caption.playback = message.playback

    def _expire_locked(self) -> bool:
        now = self.now()
        flashes = [item for item in self.flashes if item.ends_at > now]
        captions = []
        changed = len(flashes) != len(self.flashes)
        for caption in self.captions:
            if caption.resolved:
                changed = True
                continue
            if caption.expires_at is not None and caption.expires_at <= now:
                changed = True
                continue
            captions.append(caption)
        if changed:
            self.flashes = flashes
            self.captions = captions
        return changed

    def _snapshot_locked(self) -> HomeState:
        return HomeState(
            revision=self.revision,
            updated_at=self.now(),
            rooms=[room.id for room in self.home.rooms],
            lights=list(self.lights.values()),
            blinds=list(self.blinds.values()),
            media=list(self.media.values()),
            flashes=list(self.flashes),
            captions=list(self.captions),
            alerts=list(self.alerts),
            incident=self.incident,
        )

    def _touch_locked(self) -> None:
        self._expire_locked()
        self.revision += 1

    def _publish(self) -> None:
        snapshot = self.snapshot()
        for listener in list(self._listeners):
            listener(snapshot)


def _outcome_status(outcome: SpotifyOutcome) -> str:
    if outcome.status in {"succeeded", "failed", "unknown", "pending", "skipped", "cancelled"}:
        return outcome.status
    return "failed"


def build_store(
    home: HomeConfig,
    registry: Registry,
    integrations: IntegrationsConfig,
    data_dir: Path,
    asset_root: Path,
    now,
    spotify: SpotifyAdapter | None = None,
    speech: ElevenLabsAdapter | None = None,
    photon: PhotonAdapter | None = None,
) -> HomeStore:
    return HomeStore(
        home,
        registry,
        spotify or SpotifyAdapter(integrations, data_dir / "spotify_oauth.json"),
        speech or ElevenLabsAdapter(integrations),
        photon or PhotonAdapter(integrations),
        asset_root,
        now,
    )
