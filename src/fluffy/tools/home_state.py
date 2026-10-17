"""Mutable mock-home state and the handlers registered for each tool."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from fluffy.config import HomeConfig
from fluffy.tools.home_tools import (
    AlertCaregiver,
    Announce,
    DimLights,
    FlashLights,
    PlaySound,
    SetBlinds,
    SetLightWarmth,
    ShowCaption,
    StopSound,
)
from fluffy.tools.registry import registry

CAPTION_TTL = timedelta(seconds=15)


def isoformat(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    text = value.astimezone(timezone.utc).isoformat()
    if text.endswith("+00:00"):
        return text[:-6] + "Z"
    return text


@dataclass
class Effect:
    previous_state: dict[str, Any]
    new_state: dict[str, Any]
    topic: str


@dataclass
class LightState:
    luminosity: int
    warmth: str
    description: str
    color: str | None = None

    def baseline(self) -> dict[str, Any]:
        return {
            "luminosity": self.luminosity,
            "warmth": self.warmth,
            "description": self.description,
        }

    def public(self) -> dict[str, Any]:
        data = self.baseline()
        if self.color is not None:
            data["color"] = self.color
        return data

    def slice(self) -> dict[str, Any]:
        data = {"luminosity": self.luminosity, "warmth": self.warmth}
        if self.color is not None:
            data["color"] = self.color
        return data


@dataclass
class FlashState:
    color: str
    pattern: str
    until: datetime
    room: str

    def public(self) -> dict[str, Any]:
        return {
            "color": self.color,
            "pattern": self.pattern,
            "until": isoformat(self.until),
            "room": self.room,
        }


@dataclass
class CaptionState:
    text: str
    size: str
    priority: str
    until: datetime | None

    def public(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "size": self.size,
            "priority": self.priority,
            "until": None if self.until is None else isoformat(self.until),
        }


@dataclass
class AnnouncementState:
    text: str
    room: str
    at: datetime

    def public(self) -> dict[str, Any]:
        return {"text": self.text, "room": self.room, "at": isoformat(self.at)}


@dataclass
class AlertState:
    alert_id: str
    severity: str
    message: str
    include_snapshot: bool
    acknowledged: bool
    acknowledged_at: datetime | None
    created_at: datetime
    source: str

    def public(self) -> dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "severity": self.severity,
            "message": self.message,
            "include_snapshot": self.include_snapshot,
            "snapshot": None,
            "acknowledged": self.acknowledged,
            "acknowledged_at": None if self.acknowledged_at is None else isoformat(self.acknowledged_at),
            "created_at": isoformat(self.created_at),
            "source": self.source,
        }


@dataclass
class RoomState:
    name: str
    lights: dict[str, LightState]
    blinds: dict[str, int]
    speaker_device: str
    display: str
    speaker_sound: str | None = None
    speaker_volume: int = 0
    flash: FlashState | None = None
    saved_lights: dict[str, dict[str, Any]] | None = None
    caption: CaptionState | None = None
    announcement: AnnouncementState | None = None

    def speaker_public(self) -> dict[str, Any]:
        return {
            "device": self.speaker_device,
            "sound": self.speaker_sound,
            "volume": self.speaker_volume,
        }

    def public(self) -> dict[str, Any]:
        return {
            "lights": {light_id: light.public() for light_id, light in self.lights.items()},
            "blinds": {blind_id: {"position": position} for blind_id, position in self.blinds.items()},
            "speaker": self.speaker_public(),
            "display": self.display,
            "flash": None if self.flash is None else self.flash.public(),
            "caption": None if self.caption is None else self.caption.public(),
            "announcement": None if self.announcement is None else self.announcement.public(),
        }


@dataclass
class HomeState:
    rooms: dict[str, RoomState]
    caregiver_name: str
    alerts: list[AlertState] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    last_source: str | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_config(cls, home: HomeConfig, *, now: datetime | None = None) -> HomeState:
        rooms = {
            name: RoomState(
                name=name,
                lights={
                    light_id: LightState(luminosity=100, warmth="neutral", description=description)
                    for light_id, description in room.lights.items()
                },
                blinds={blind_id: 100 for blind_id in room.blinds},
                speaker_device=room.speaker,
                display=room.display,
            )
            for name, room in home.rooms.items()
        }
        return cls(rooms=rooms, caregiver_name=home.caregiver_name, updated_at=now)

    def snapshot(self) -> dict[str, Any]:
        return {
            "rooms": {name: room.public() for name, room in self.rooms.items()},
            "caregiver": {"name": self.caregiver_name},
            "alerts": [alert.public() for alert in self.alerts],
            "events": list(self.events),
            "last_source": self.last_source,
            "updated_at": None if self.updated_at is None else isoformat(self.updated_at),
        }

    def note(self, now: datetime, source: str) -> None:
        self.updated_at = now
        self.last_source = source

    def dim_lights(self, args: DimLights, now: datetime) -> Effect:
        self.updated_at = now
        room = self.rooms[args.room]
        targets = [args.light] if args.light is not None else list(room.lights)
        previous = {light_id: room.lights[light_id].slice() for light_id in targets}
        for light_id in targets:
            room.lights[light_id].luminosity = args.luminosity
            saved = room.saved_lights
            if saved is not None and light_id in saved:
                saved[light_id]["luminosity"] = args.luminosity
        current = {light_id: room.lights[light_id].slice() for light_id in targets}
        return Effect(
            {"room": args.room, "lights": previous},
            {"room": args.room, "lights": current},
            "device",
        )

    def set_light_warmth(self, args: SetLightWarmth, now: datetime) -> Effect:
        self.updated_at = now
        room = self.rooms[args.room]
        previous = {light_id: light.slice() for light_id, light in room.lights.items()}
        for light_id, light in room.lights.items():
            light.warmth = args.warmth
            if room.saved_lights is not None and light_id in room.saved_lights:
                room.saved_lights[light_id]["warmth"] = args.warmth
        current = {light_id: light.slice() for light_id, light in room.lights.items()}
        return Effect(
            {"room": args.room, "lights": previous},
            {"room": args.room, "lights": current},
            "device",
        )

    def set_blinds(self, args: SetBlinds, now: datetime) -> Effect:
        self.updated_at = now
        room = self.rooms[args.room]
        previous = {blind_id: {"position": position} for blind_id, position in room.blinds.items()}
        for blind_id in room.blinds:
            room.blinds[blind_id] = args.position
        current = {blind_id: {"position": position} for blind_id, position in room.blinds.items()}
        return Effect(
            {"room": args.room, "blinds": previous},
            {"room": args.room, "blinds": current},
            "device",
        )

    def play_sound(self, args: PlaySound, now: datetime) -> Effect:
        self.updated_at = now
        room = self.rooms[args.room]
        previous = room.speaker_public()
        room.speaker_sound = args.sound
        room.speaker_volume = args.volume
        return Effect(
            {"room": args.room, "speaker": previous},
            {"room": args.room, "speaker": room.speaker_public()},
            "device",
        )

    def stop_sound(self, args: StopSound, now: datetime) -> Effect:
        self.updated_at = now
        room = self.rooms[args.room]
        previous = room.speaker_public()
        room.speaker_sound = None
        room.speaker_volume = 0
        return Effect(
            {"room": args.room, "speaker": previous},
            {"room": args.room, "speaker": room.speaker_public()},
            "device",
        )

    def flash_lights(self, args: FlashLights, now: datetime) -> Effect:
        self.updated_at = now
        room = self.rooms[args.room]
        previous = {
            "room": args.room,
            "flash": None if room.flash is None else room.flash.public(),
            "lights": {light_id: light.public() for light_id, light in room.lights.items()},
        }
        if room.saved_lights is None:
            room.saved_lights = {light_id: light.baseline() for light_id, light in room.lights.items()}
        room.flash = FlashState(
            color=args.color,
            pattern=args.pattern,
            until=now + timedelta(seconds=args.duration_s),
            room=args.room,
        )
        for light in room.lights.values():
            light.color = args.color
        return Effect(previous, self._flash_slice(room), "device")

    def show_caption(self, args: ShowCaption, now: datetime) -> Effect:
        self.updated_at = now
        until = None if args.priority == "high" else now + CAPTION_TTL
        previous = self._captions()
        caption = CaptionState(text=args.text, size=args.size, priority=args.priority, until=until)
        for room in self.rooms.values():
            room.caption = caption
        return Effect({"captions": previous}, {"captions": self._captions()}, "device")

    def announce(self, args: Announce, now: datetime) -> Effect:
        self.updated_at = now
        room = self.rooms[args.room]
        previous = None if room.announcement is None else room.announcement.public()
        room.announcement = AnnouncementState(text=args.text, room=args.room, at=now)
        return Effect(
            {"room": args.room, "announcement": previous},
            {"room": args.room, "announcement": room.announcement.public()},
            "device",
        )

    def alert_caregiver(self, args: AlertCaregiver, now: datetime) -> Effect:
        self.updated_at = now
        alert = AlertState(
            alert_id="alt_" + uuid.uuid4().hex[:12],
            severity=args.severity,
            message=args.message,
            include_snapshot=args.include_snapshot,
            acknowledged=False,
            acknowledged_at=None,
            created_at=now,
            source=self.last_source or "manual",
        )
        self.alerts.append(alert)
        return Effect({"alert": None}, {"alert": alert.public()}, "caregiver")

    def acknowledge(self, alert_id: str, now: datetime, source: str) -> Effect:
        alert = self._alert(alert_id)
        self.note(now, source)
        previous = alert.public()
        if not alert.acknowledged:
            alert.acknowledged = True
            alert.acknowledged_at = now
        return Effect({"alert": previous}, {"alert": alert.public()}, "caregiver")

    def dismiss_caption(self, now: datetime, source: str) -> Effect:
        if not any(room.caption is not None for room in self.rooms.values()):
            raise KeyError("no caption is showing")
        self.note(now, source)
        previous = self._captions()
        for room in self.rooms.values():
            room.caption = None
        return Effect({"captions": previous}, {"captions": self._captions()}, "device")

    def doorbell(self, room: str, now: datetime, source: str) -> dict[str, Any]:
        if room not in self.rooms:
            raise KeyError(f"unknown room: {room}")
        self.note(now, source)
        event = {"event": "doorbell", "room": room, "at": isoformat(now), "source": source}
        self.events.append(event)
        del self.events[:-20]
        return event

    def expire(self, now: datetime) -> list[Effect]:
        effects: list[Effect] = []
        for room in self.rooms.values():
            if room.flash is not None and now >= room.flash.until:
                self.note(now, "system")
                previous = self._flash_slice(room)
                self._restore_room(room)
                effects.append(Effect(previous, self._flash_slice(room), "device"))
            if room.caption is not None and room.caption.until is not None and now >= room.caption.until:
                self.note(now, "system")
                previous = self._captions()
                room.caption = None
                effects.append(Effect({"captions": previous}, {"captions": self._captions()}, "device"))
        return effects

    def _alert(self, alert_id: str) -> AlertState:
        for alert in self.alerts:
            if alert.alert_id == alert_id:
                return alert
        raise KeyError(f"unknown alert: {alert_id}")

    def _captions(self) -> dict[str, Any]:
        return {
            name: None if room.caption is None else room.caption.public()
            for name, room in self.rooms.items()
        }

    def _flash_slice(self, room: RoomState) -> dict[str, Any]:
        return {
            "room": room.name,
            "flash": None if room.flash is None else room.flash.public(),
            "lights": {light_id: light.public() for light_id, light in room.lights.items()},
        }

    def _restore_room(self, room: RoomState) -> None:
        saved = room.saved_lights or {}
        for light_id, light in room.lights.items():
            baseline = saved.get(light_id)
            if baseline is not None:
                light.luminosity = int(baseline["luminosity"])
                light.warmth = str(baseline["warmth"])
            light.color = None
        room.saved_lights = None
        room.flash = None


registry.set_handler("dim_lights", HomeState.dim_lights)
registry.set_handler("set_light_warmth", HomeState.set_light_warmth)
registry.set_handler("set_blinds", HomeState.set_blinds)
registry.set_handler("play_sound", HomeState.play_sound)
registry.set_handler("stop_sound", HomeState.stop_sound)
registry.set_handler("flash_lights", HomeState.flash_lights)
registry.set_handler("show_caption", HomeState.show_caption)
registry.set_handler("announce", HomeState.announce)
registry.set_handler("alert_caregiver", HomeState.alert_caregiver)
