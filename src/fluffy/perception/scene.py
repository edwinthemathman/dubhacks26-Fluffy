"""SceneState, the structured description Perceptron returns for a frame."""

from typing import Literal

from pydantic import BaseModel, Field


class PersonState(BaseModel):
    posture: Literal["lying", "sitting", "standing", "walking", "unknown"] = "unknown"
    on_floor: bool = False
    location_surface: Literal["floor", "sofa", "bed", "unknown"] = "unknown"
    eyes: Literal["open", "closed", "unknown"] = "unknown"
    gesture: Literal["none", "pointing", "shielding_eyes", "waving"] = "none"
    attention_target: str | None = None


class ObjectSighting(BaseModel):
    name: str
    location_desc: str


class SceneState(BaseModel):
    people: list[PersonState] = Field(default_factory=list)
    objects_seen: list[ObjectSighting] = Field(default_factory=list)
    text_present: str | None = None
    lighting: Literal["dark", "dim", "normal", "bright"] = "normal"
    summary: str = ""
    confidence: float = 0.0


def scene_json_schema() -> dict:
    """Schema for a Perceptron perception call. That call uses no tools."""
    return SceneState.model_json_schema()
