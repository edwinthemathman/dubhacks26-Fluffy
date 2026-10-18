"""Scene contract exported for Part 2.

The fields follow Part 2's SceneState. Identifiers are snake_case
(``shielding_eyes``, ``pill_organizer``). Extra fields are allowed so
perception can extend a record without a new execution release.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Gesture = Literal["pointing", "shielding_eyes", "waving"]
LocationSurface = Literal["floor", "sofa", "bed"]
TrackedObject = Literal[
    "keys",
    "phone",
    "glasses",
    "wallet",
    "pill_organizer",
    "cane",
    "hearing_aids",
]


class ScenePerson(BaseModel):
    model_config = ConfigDict(extra="allow")
    posture: str
    on_floor: bool
    location_surface: LocationSurface
    eyes: str | None = None
    motion: Literal["none", "small", "purposeful"] | None = None
    activity_hint: Literal["none", "reaching", "picking_up", "exercising", "playing_on_floor"] | None = None
    fall_confidence: float | None = Field(default=None, ge=0, le=1)
    gesture: Gesture | None = None
    attention_target: str | None = Field(
        default=None,
        description="Device id from home.toml, when the person is attending to one.",
    )


class SceneObject(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: TrackedObject
    location_desc: str


class SceneState(BaseModel):
    model_config = ConfigDict(extra="allow")
    room: str | None = None
    people: list[ScenePerson] = Field(default_factory=list)
    objects_seen: list[SceneObject] = Field(default_factory=list)
    text_present: str | bool | None = Field(
        default=None,
        description="Visible text, or a boolean when only presence is known.",
    )
    lighting: str | None = None
    summary: str
    confidence: float = Field(ge=0, le=1)


def scene_schema() -> dict[str, Any]:
    schema = SceneState.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "SceneState"
    return schema
