"""Single registry for tool names, argument validation, and model schemas.

Handlers do not live here. The output service dispatches by the same names.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from fluffy.config import HomeConfig

ActionClass = Literal["comfort", "notify", "safety"]
EffectKind = Literal["simulated_state", "browser_audio", "spotify", "photon"]


class ToolError(Exception):
    def __init__(self, error_code: str, message: str) -> None:
        self.error_code = error_code
        self.message = message
        super().__init__(message)


class ToolSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    name: str
    action_class: ActionClass
    effect_kind: EffectKind
    summary: str
    model: type[BaseModel]
    auto_enabled: bool = True
    requires_provider: Literal["none", "spotify", "elevenlabs", "photon", "spotify_or_local"] = "none"


class Registry:
    def __init__(self, home: HomeConfig, tools: list[ToolSpec]) -> None:
        self.home = home
        self._tools = {tool.name: tool for tool in tools}

    def names(self) -> list[str]:
        return list(self._tools)

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolError("unknown_tool", f"unknown tool {name}") from exc

    def validate_args(self, name: str, args: dict[str, Any]) -> BaseModel:
        spec = self.get(name)
        if not isinstance(args, dict):
            raise ToolError("invalid_arguments", "arguments must be an object")
        try:
            return spec.model.model_validate(args)
        except ValidationError as exc:
            raise ToolError("invalid_arguments", exc.errors()[0]["msg"]) from exc

    def manifest(self, enabled: dict[str, bool]) -> dict[str, Any]:
        tools = []
        for spec in self._tools.values():
            schema = spec.model.model_json_schema()
            schema.pop("title", None)
            _prefer_enums(schema)
            tools.append(
                {
                    "name": spec.name,
                    "action_class": spec.action_class,
                    "effect_kind": spec.effect_kind,
                    "summary": spec.summary,
                    "enabled": enabled.get(spec.name, True),
                    "parameters": schema,
                }
            )
        return {"schema_version": "2.0", "tools": tools}


def _model(name: str, **fields: Any) -> type[BaseModel]:
    return create_model(name, __config__=ConfigDict(extra="forbid"), **fields)


def build_registry(home: HomeConfig) -> Registry:
    rooms = tuple(home.room_ids)
    lights = tuple(home.light_ids)
    sounds = tuple(home.media_ids)
    if not rooms or not lights or not sounds:
        raise ValueError("registry requires a room, a light, and a media id")
    room_t = Literal[*rooms]
    light_t = Literal[*lights]
    sound_t = Literal[*sounds]

    Dim = _model(
        "DimLightsArgs",
        room=(room_t, ...),
        luminosity=(int, Field(ge=0, le=100, strict=True)),
        light=(light_t | None, None),
    )
    Warmth = _model(
        "SetLightWarmthArgs",
        room=(room_t, ...),
        warmth=(Literal["warm", "neutral", "cool"], ...),
        light=(light_t | None, None),
    )
    Blinds = _model(
        "SetBlindsArgs",
        room=(room_t, ...),
        position=(int, Field(ge=0, le=100, strict=True)),
    )
    Play = _model(
        "PlaySoundArgs",
        room=(room_t, ...),
        sound=(sound_t, ...),
        volume=(int, Field(ge=0, le=100, strict=True)),
    )
    Stop = _model("StopSoundArgs", room=(room_t, ...))
    Flash = _model(
        "FlashLightsArgs",
        room=(room_t, ...),
        color=(Literal["blue", "amber", "white"], ...),
        pulses=(int, Field(ge=1, le=3, strict=True)),
    )
    Caption = _model(
        "ShowCaptionArgs",
        text=(str, Field(min_length=1, max_length=240)),
        priority=(Literal["normal", "high"], ...),
        ttl_s=(int, Field(default=10, ge=3, le=30, strict=True)),
    )
    Announce = _model(
        "AnnounceArgs",
        text=(str, Field(min_length=1, max_length=home.max_utterance_chars)),
        room=(room_t, ...),
    )
    Alert = _model(
        "AlertCaregiverArgs",
        incident_id=(str, Field(min_length=4, max_length=80, pattern=r"^inc_[A-Za-z0-9_\-]+$")),
        severity=(Literal["info", "high"], ...),
        message=(str, Field(min_length=1, max_length=500)),
    )
    Volume = _model(
        "SetVolumeArgs",
        room=(room_t, ...),
        volume=(int, Field(ge=0, le=100, strict=True)),
    )

    tools = [
        ToolSpec(name="dim_lights", action_class="comfort", effect_kind="simulated_state", summary="Set lamp brightness. Zero is off.", model=Dim),
        ToolSpec(name="set_light_warmth", action_class="comfort", effect_kind="simulated_state", summary="Set lamp warmth to a fixed color.", model=Warmth),
        ToolSpec(name="set_blinds", action_class="comfort", effect_kind="simulated_state", summary="Move blinds. Zero is closed.", model=Blinds),
        ToolSpec(name="play_sound", action_class="comfort", effect_kind="spotify", summary="Play a configured media id.", model=Play, requires_provider="spotify_or_local"),
        ToolSpec(name="stop_sound", action_class="comfort", effect_kind="spotify", summary="Pause Spotify and stop local sound.", model=Stop),
        ToolSpec(name="flash_lights", action_class="notify", effect_kind="simulated_state", summary="Temporary lamp overlay. Does not change brightness.", model=Flash),
        ToolSpec(name="show_caption", action_class="notify", effect_kind="simulated_state", summary="Silent caption. Does not synthesize speech.", model=Caption),
        ToolSpec(name="announce", action_class="notify", effect_kind="browser_audio", summary="Caption plus one spoken utterance.", model=Announce, requires_provider="elevenlabs"),
        ToolSpec(name="alert_caregiver", action_class="safety", effect_kind="photon", summary="Message the configured trusted contact about an eligible incident.", model=Alert, auto_enabled=False, requires_provider="photon"),
        ToolSpec(name="set_volume", action_class="comfort", effect_kind="spotify", summary="Set Spotify device volume after verification.", model=Volume, requires_provider="spotify"),
    ]
    return Registry(home, tools)


def _prefer_enums(schema: Any) -> None:
    if isinstance(schema, dict):
        if "const" in schema and "enum" not in schema:
            schema["enum"] = [schema.pop("const")]
        for value in schema.values():
            _prefer_enums(value)
    elif isinstance(schema, list):
        for item in schema:
            _prefer_enums(item)


def canonical_args(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json", exclude_none=False)
