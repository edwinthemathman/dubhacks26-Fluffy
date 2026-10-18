"""Inference-side view of the Part 1 tool contract.

The canonical registry (handlers, home config, schema export) belongs to
Part 1. This catalog is the subset a decision call needs: names, argument
checks, action class, and the function schemas sent to Perceptron.

When the execution registry should own the newer device tools, copy
open_door, close_door, play_music, stop_music, send_text, and call_person
into src/fluffy/tools/home_tools.py. Do not rename the tools already used
in decide.py.
"""

import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, create_model

ActionClass = Literal["comfort", "notify", "safety", "meta"]


class ToolError(Exception):
    def __init__(self, name: str, detail: str) -> None:
        self.name = name
        self.detail = detail
        super().__init__(f"{name}: {detail}")


class DimLightsArgs(BaseModel):
    room: Literal["living_room"]
    luminosity: int = Field(ge=0, le=100)
    light: Literal["lamp.living_floor", "light.living_ceiling"] | None = None


class SetLightWarmthArgs(BaseModel):
    room: Literal["living_room"]
    warmth: Literal["warm", "neutral", "cool"]


class SetBlindsArgs(BaseModel):
    room: Literal["living_room"]
    position: int = Field(ge=0, le=100)


class PlaySoundArgs(BaseModel):
    sound: Literal["music", "rain", "chime", "white_noise", "doorbell"]
    room: Literal["living_room"]
    volume: int = Field(ge=0, le=100)


class StopSoundArgs(BaseModel):
    room: Literal["living_room"]


class DoorArgs(BaseModel):
    door: Literal["front_door", "bedroom_door"]


class PlayMusicArgs(BaseModel):
    query: str | None = None
    volume: int | None = Field(default=None, ge=0, le=100)


class StopMusicArgs(BaseModel):
    pass


class SendTextArgs(BaseModel):
    to: str
    message: str = Field(max_length=300)


class CallPersonArgs(BaseModel):
    to: str


class FlashLightsArgs(BaseModel):
    room: Literal["living_room"]
    color: Literal["blue", "red", "amber", "white"]
    pattern: Literal["pulse1", "pulse3", "strobe"]
    duration_s: int = Field(ge=1, le=10)


class ShowCaptionArgs(BaseModel):
    text: str = Field(max_length=140)
    size: Literal["large", "xl"]
    priority: Literal["normal", "high"]


class AnnounceArgs(BaseModel):
    text: str
    room: Literal["living_room"]


class AlertCaregiverArgs(BaseModel):
    severity: Literal["info", "high"]
    message: str
    include_snapshot: bool = False


class DescribeSceneArgs(BaseModel):
    detail: str = "normal"


class FindObjectArgs(BaseModel):
    name: str


class ReadTextArgs(BaseModel):
    pass


class LookArgs(BaseModel):
    question: str


class AskUserArgs(BaseModel):
    question: str = Field(
        description=(
            "One short question about the action, using the words they said. "
            'Good: "Should I put on some Spotify?" '
            'Good: "Should I dim the lights and put on music?" '
            "Do not describe what you saw."
        )
    )
    choices: list[str] | None = None


class ConfirmPlanArgs(BaseModel):
    plan_id: str
    answer: Literal["yes", "no"]


@dataclass(frozen=True)
class ParsedCall:
    name: str
    args: dict[str, Any]
    confidence: float
    reason: str
    action_class: ActionClass


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    action_class: ActionClass
    args_model: type[BaseModel]
    call_model: type[BaseModel]


def _device_call_model(args_model: type[BaseModel]) -> type[BaseModel]:
    """Device tools gain confidence and reason, which are stripped before a plan is built."""
    return create_model(
        f"{args_model.__name__}Call",
        __base__=args_model,
        confidence=(
            float,
            Field(
                ge=0,
                le=1,
                description="0.8-1.0 plainly seen or heard, 0.5-0.7 likely, lower if guessing.",
            ),
        ),
        reason=(
            str,
            Field(description="At most 6 words on what you saw or heard, e.g. 'fell, not getting up'."),
        ),
    )


def _spec(
    name: str,
    description: str,
    action_class: ActionClass,
    args_model: type[BaseModel],
) -> ToolSpec:
    call_model = args_model if action_class == "meta" else _device_call_model(args_model)
    return ToolSpec(name, description, action_class, args_model, call_model)


SPECS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in [
        _spec(
            "dim_lights",
            "Change a light's brightness, 0 is off. Use 10-30 when someone dozes off, "
            "30-40 on a light they shield their eyes from, 70-100 in a dark room or on arrival. "
            "A proposal: the person is asked first.",
            "comfort",
            DimLightsArgs,
        ),
        _spec(
            "set_light_warmth",
            "Change a light's warmth. Warm when someone is winding down or falling asleep.",
            "comfort",
            SetLightWarmthArgs,
        ),
        _spec("set_blinds", "Move the blinds. 0 is closed, 100 is open.", "comfort", SetBlindsArgs),
        _spec(
            "play_sound",
            "Play a sound in the room. Use music when they ask for music. "
            "Use rain, white_noise, or chime for sleep or a soft background. "
            "Call this in the same turn as dim_lights or another comfort tool when the request asks for both.",
            "comfort",
            PlaySoundArgs,
        ),
        _spec("stop_sound", "Stop the sound playing in the room.", "comfort", StopSoundArgs),
        _spec("open_door", "Open a door in the mock home. front_door or bedroom_door.", "comfort", DoorArgs),
        _spec("close_door", "Close a door in the mock home. front_door or bedroom_door.", "comfort", DoorArgs),
        _spec(
            "play_music",
            "Play a song on Spotify. query is the song or mood. The person is asked first.",
            "comfort",
            PlayMusicArgs,
        ),
        _spec("stop_music", "Pause Spotify.", "comfort", StopMusicArgs),
        _spec(
            "send_text",
            "Text a saved contact. Always ask first, even when they clearly asked you to send it.",
            "comfort",
            SendTextArgs,
        ),
        _spec(
            "call_person",
            "Show a demo call animation. Photon does not place calls. Label it call (demo).",
            "comfort",
            CallPersonArgs,
        ),
        _spec(
            "flash_lights",
            "Pulse the lights as an alert, then restore them. Use for a doorbell, knock, or alarm, "
            "especially for someone Deaf or hard of hearing.",
            "notify",
            FlashLightsArgs,
        ),
        _spec(
            "show_caption",
            "Show a caption on the companion screen. Pair with flash_lights to say what happened.",
            "notify",
            ShowCaptionArgs,
        ),
        _spec("announce", "Speak a short line in the room.", "notify", AnnounceArgs),
        _spec(
            "alert_caregiver",
            "Get the caregiver. Use when someone falls, ends up on the floor, stays down, "
            "struggles to get up, or looks hurt or in pain, even if they never say help. "
            "The person is asked first.",
            "safety",
            AlertCaregiverArgs,
        ),
        _spec("describe_scene", "Describe where things are relative to the person.", "meta", DescribeSceneArgs),
        _spec("find_object", "Find an object in memory or in the live scene.", "meta", FindObjectArgs),
        _spec("read_text", "Read text the camera can see.", "meta", ReadTextArgs),
        _spec("look", "Answer a visual question about the current scene.", "meta", LookArgs),
        _spec(
            "ask_user",
            "Ask one short question about an action, like \"Should I put on some Spotify?\" "
            "or \"Should I dim the lights and put on music?\". "
            "Use the words they said. Do not describe what you saw.",
            "meta",
            AskUserArgs,
        ),
        _spec("confirm_plan", "Record a yes or no for a plan that is waiting.", "meta", ConfirmPlanArgs),
    ]
}

META_TOOLS = {name for name, spec in SPECS.items() if spec.action_class == "meta"}
DEVICE_TOOLS = set(SPECS) - META_TOOLS


def perceptron_tools() -> list[dict]:
    """Function schemas for a decision call. Do not combine these with a response schema."""
    tools = []
    for spec in SPECS.values():
        schema = spec.call_model.model_json_schema()
        schema.pop("title", None)
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": schema,
                },
            }
        )
    return tools


def parse_call(name: str, raw_arguments: str) -> ParsedCall:
    spec = SPECS.get(name)
    if spec is None:
        raise ToolError(name, "unknown tool")
    text = raw_arguments.strip()
    if not text:
        if spec.action_class != "meta":
            raise ToolError(name, "malformed arguments")
        text = "{}"
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ToolError(name, "malformed arguments") from exc
    if not isinstance(data, dict):
        raise ToolError(name, "arguments must be a JSON object")
    try:
        model = spec.call_model.model_validate(data)
    except ValidationError as exc:
        raise ToolError(name, exc.errors()[0]["msg"]) from exc
    dumped = model.model_dump(exclude_none=True)
    confidence = float(dumped.pop("confidence", 1.0))
    reason = str(dumped.pop("reason", ""))
    return ParsedCall(name, dumped, confidence, reason, spec.action_class)


def validate_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Check arguments the harness built itself, which have no confidence field."""
    spec = SPECS.get(name)
    if spec is None:
        raise ToolError(name, "unknown tool")
    try:
        model = spec.args_model.model_validate(args)
    except ValidationError as exc:
        raise ToolError(name, exc.errors()[0]["msg"]) from exc
    return model.model_dump(exclude_none=True)


def action_class(name: str) -> ActionClass:
    spec = SPECS.get(name)
    if spec is None:
        raise ToolError(name, "unknown tool")
    return spec.action_class
