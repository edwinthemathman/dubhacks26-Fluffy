"""Load and validate ``config/home.toml``."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ConfigError(Exception):
    def __init__(self, messages: list[str]) -> None:
        self.messages = messages
        super().__init__("\n".join(messages))


class _RawRoom(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lights: dict[str, str]
    blinds: list[str]
    speaker: str
    display: str


class _RawSounds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    names: list[str]


class _RawAlerts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    colors: list[str]
    patterns: list[str]


class _RawCaregiver(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)


class _RawHome(BaseModel):
    model_config = ConfigDict(extra="forbid")
    room: dict[str, _RawRoom]
    sounds: _RawSounds
    alerts: _RawAlerts
    caregiver: _RawCaregiver


@dataclass(frozen=True)
class RoomConfig:
    name: str
    lights: dict[str, str]
    blinds: tuple[str, ...]
    speaker: str
    display: str


@dataclass(frozen=True)
class HomeConfig:
    """Devices and enums the tools are allowed to name."""

    rooms: dict[str, RoomConfig]
    sounds: tuple[str, ...]
    colors: tuple[str, ...]
    patterns: tuple[str, ...]
    caregiver_name: str
    path: Path

    @property
    def room_names(self) -> tuple[str, ...]:
        return tuple(self.rooms)

    @property
    def light_ids(self) -> tuple[str, ...]:
        return tuple(light_id for room in self.rooms.values() for light_id in room.lights)

    @property
    def blind_ids(self) -> tuple[str, ...]:
        return tuple(blind_id for room in self.rooms.values() for blind_id in room.blinds)

    @property
    def speaker_ids(self) -> tuple[str, ...]:
        return tuple(room.speaker for room in self.rooms.values())

    @property
    def display_ids(self) -> tuple[str, ...]:
        return tuple(room.display for room in self.rooms.values())

    def lights_in(self, room: str) -> tuple[str, ...]:
        return tuple(self.rooms[room].lights)

    def enum_values(self, kind: str) -> list[str]:
        values = {
            "rooms": self.room_names,
            "lights": self.light_ids,
            "blinds": self.blind_ids,
            "sounds": self.sounds,
            "colors": self.colors,
            "patterns": self.patterns,
            "warmth": ("warm", "neutral", "cool"),
            "caption_size": ("large", "xl"),
            "severity": ("info", "high"),
        }
        try:
            return list(values[kind])
        except KeyError as exc:
            raise KeyError(f"unknown enum kind: {kind}") from exc

    def devices_manifest(self) -> dict[str, Any]:
        lights: dict[str, str] = {}
        for room in self.rooms.values():
            lights.update(room.lights)
        return {
            "rooms": list(self.room_names),
            "lights": lights,
            "blinds": list(self.blind_ids),
            "speakers": list(self.speaker_ids),
            "displays": list(self.display_ids),
            "sounds": list(self.sounds),
            "colors": list(self.colors),
            "patterns": list(self.patterns),
            "caregiver": self.caregiver_name,
        }


def default_config_path() -> Path:
    """Find ``config/home.toml`` from the environment, cwd, or this repo."""

    env = os.environ.get("FLUFFY_HOME_TOML")
    candidates: list[Path] = []
    if env:
        candidates.append(Path(env))
    candidates.append(Path.cwd() / "config" / "home.toml")
    candidates.append(Path(__file__).resolve().parents[2] / "config" / "home.toml")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def load_home(path: Path | None = None) -> HomeConfig:
    """Load the home configuration and reject anything the tools cannot name."""

    config_path = path or default_config_path()
    if not config_path.is_file():
        raise ConfigError([f"home config not found: {config_path}"])
    try:
        raw_text = config_path.read_text(encoding="utf-8")
        parsed = tomllib.loads(raw_text)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError([f"{config_path}: {exc}"]) from exc
    try:
        raw = _RawHome.model_validate(parsed)
    except ValidationError as exc:
        messages = [f"{config_path}: {error['loc']}: {error['msg']}" for error in exc.errors()]
        raise ConfigError(messages) from exc

    messages: list[str] = []
    if not raw.room:
        messages.append(f"{config_path}: at least one room is required")
    rooms: dict[str, RoomConfig] = {}
    seen_ids: dict[str, str] = {}
    for room_name, room in raw.room.items():
        _check_token(messages, config_path, f"room {room_name}", room_name)
        if not room.lights:
            messages.append(f"{config_path}: room {room_name} needs at least one light")
        for light_id, description in room.lights.items():
            _check_token(messages, config_path, "light", light_id)
            if not description.strip():
                messages.append(f"{config_path}: light {light_id} needs a description")
            _remember(messages, config_path, seen_ids, light_id, f"light in {room_name}")
        for blind_id in room.blinds:
            _check_token(messages, config_path, "blind", blind_id)
            _remember(messages, config_path, seen_ids, blind_id, f"blind in {room_name}")
        _check_token(messages, config_path, "speaker", room.speaker)
        _remember(messages, config_path, seen_ids, room.speaker, f"speaker in {room_name}")
        _check_token(messages, config_path, "display", room.display)
        _remember(messages, config_path, seen_ids, room.display, f"display in {room_name}")
        rooms[room_name] = RoomConfig(
            name=room_name,
            lights=dict(room.lights),
            blinds=tuple(room.blinds),
            speaker=room.speaker,
            display=room.display,
        )
    sounds = _unique_list(messages, config_path, "sound", raw.sounds.names)
    colors = _unique_list(messages, config_path, "alert color", raw.alerts.colors)
    patterns = _unique_list(messages, config_path, "alert pattern", raw.alerts.patterns)
    if not raw.caregiver.name.strip():
        messages.append(f"{config_path}: caregiver name is required")
    if messages:
        raise ConfigError(messages)
    return HomeConfig(
        rooms=rooms,
        sounds=tuple(sounds),
        colors=tuple(colors),
        patterns=tuple(patterns),
        caregiver_name=raw.caregiver.name.strip(),
        path=config_path,
    )


def _check_token(messages: list[str], path: Path, label: str, value: str) -> None:
    if not value or value.strip() != value or any(char.isspace() for char in value):
        messages.append(f"{path}: {label} id {value!r} must be a non-empty token")


def _remember(
    messages: list[str],
    path: Path,
    seen: dict[str, str],
    device_id: str,
    role: str,
) -> None:
    previous = seen.get(device_id)
    if previous is not None:
        messages.append(f"{path}: device id {device_id!r} is used as {previous} and {role}")
        return
    seen[device_id] = role


def _unique_list(messages: list[str], path: Path, label: str, values: list[str]) -> list[str]:
    if not values:
        messages.append(f"{path}: at least one {label} is required")
    found: set[str] = set()
    ordered: list[str] = []
    for value in values:
        _check_token(messages, path, label, value)
        if value in found:
            messages.append(f"{path}: duplicate {label} {value!r}")
            continue
        found.add(value)
        ordered.append(value)
    return ordered
