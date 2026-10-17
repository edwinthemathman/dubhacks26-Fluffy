"""Argument models for the living-room tools.

``confidence`` and ``reason`` are not fields here. The registry adds them to
the model-facing schema and strips them before a plan is built.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

from fluffy.config import HomeConfig
from fluffy.tools.registry import tool


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enum_fields: ClassVar[dict[str, str]] = {}

    @model_validator(mode="after")
    def _known_names(self, info: ValidationInfo) -> ToolArgs:
        home = _home(info)
        for field_name, kind in type(self).enum_fields.items():
            value = getattr(self, field_name)
            if value is None:
                continue
            if value not in home.enum_values(kind):
                raise ValueError(f"unknown {field_name}: {value}")
        return self


def _home(info: ValidationInfo) -> HomeConfig:
    context = info.context or {}
    home = context.get("home")
    if not isinstance(home, HomeConfig):
        raise ValueError("home configuration is not loaded")
    return home


@tool(
    name="dim_lights",
    action_class="comfort",
    description="Set luminosity from 0 (off) to 100. Omit light to change every light in the room.",
)
class DimLights(ToolArgs):
    enum_fields: ClassVar[dict[str, str]] = {"room": "rooms", "light": "lights"}
    room: str
    luminosity: int = Field(ge=0, le=100, description="0 is off and 100 is full brightness.")
    light: str | None = Field(default=None, description="A light id in the room. Omit for every light.")

    @model_validator(mode="after")
    def _light_belongs_to_room(self, info: ValidationInfo) -> DimLights:
        home = _home(info)
        if self.light is not None and self.room in home.rooms and self.light not in home.lights_in(self.room):
            raise ValueError(f"light {self.light} is not in room {self.room}")
        return self


@tool(
    name="set_light_warmth",
    action_class="comfort",
    description="Set the warmth of every light in the room to warm, neutral, or cool.",
)
class SetLightWarmth(ToolArgs):
    enum_fields: ClassVar[dict[str, str]] = {"room": "rooms", "warmth": "warmth"}
    room: str
    warmth: str


@tool(
    name="set_blinds",
    action_class="comfort",
    description="Set the blinds in a room from 0 (closed) to 100 (open).",
)
class SetBlinds(ToolArgs):
    enum_fields: ClassVar[dict[str, str]] = {"room": "rooms"}
    room: str
    position: int = Field(ge=0, le=100)

    @model_validator(mode="after")
    def _room_has_blinds(self, info: ValidationInfo) -> SetBlinds:
        home = _home(info)
        room = home.rooms.get(self.room)
        if room is not None and not room.blinds:
            raise ValueError(f"room {self.room} has no blinds")
        return self


@tool(
    name="play_sound",
    action_class="comfort",
    description="Play a named sound in a room at a volume from 0 to 100.",
)
class PlaySound(ToolArgs):
    enum_fields: ClassVar[dict[str, str]] = {"sound": "sounds", "room": "rooms"}
    sound: str
    room: str
    volume: int = Field(ge=0, le=100)


@tool(
    name="stop_sound",
    action_class="comfort",
    description="Stop the sound playing in a room.",
)
class StopSound(ToolArgs):
    enum_fields: ClassVar[dict[str, str]] = {"sound": "sounds", "room": "rooms"}
    sound: str
    room: str
    volume: int = Field(ge=0, le=100)


@tool(
    name="flash_lights",
    action_class="notify",
    description="Flash the lights in a room, then restore their previous state. duration_s is at most 10.",
)
class FlashLights(ToolArgs):
    enum_fields: ClassVar[dict[str, str]] = {
        "room": "rooms",
        "color": "colors",
        "pattern": "patterns",
    }
    room: str
    color: str
    pattern: str
    duration_s: float = Field(gt=0, le=10)


@tool(
    name="show_caption",
    action_class="notify",
    description=(
        "Show a caption of at most 140 characters. "
        "Priority high stays until dismissed; any other priority clears after 15 seconds."
    ),
)
class ShowCaption(ToolArgs):
    enum_fields: ClassVar[dict[str, str]] = {"size": "caption_size"}
    text: str = Field(min_length=1, max_length=140)
    size: str
    priority: str = Field(default="normal", min_length=1)


@tool(
    name="announce",
    action_class="notify",
    description="Speak text in a room with browser text-to-speech.",
)
class Announce(ToolArgs):
    enum_fields: ClassVar[dict[str, str]] = {"room": "rooms"}
    text: str = Field(min_length=1)
    room: str


@tool(
    name="alert_caregiver",
    action_class="safety",
    description=(
        "Record an alert for the caregiver. include_snapshot defaults to false. "
        "This subsystem never attaches an image or contacts a real person."
    ),
)
class AlertCaregiver(ToolArgs):
    enum_fields: ClassVar[dict[str, str]] = {"severity": "severity"}
    severity: str
    message: str = Field(min_length=1)
    include_snapshot: bool = False
