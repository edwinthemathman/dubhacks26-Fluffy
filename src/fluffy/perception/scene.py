"""SceneState, the structured description Perceptron returns for a frame."""

from typing import Literal

from pydantic import BaseModel, Field


Activity = Literal["sleeping", "arriving", "leaving", "idle", "distressed", "fallen", "unknown"]
# Sound backs up what is seen. It breaks ties between look-alike moments
# (snoring versus speech), and pain or a thud can confirm a fall nobody names.
SoundLabel = Literal[
    "snoring", "door", "help", "pain", "impact", "doorbell", "knock", "alarm", "speech", "none"
]
# Visible signs that someone is hurt. None of these need words.
DistressSign = Literal[
    "grimacing",
    "holding_body",
    "not_getting_up",
    "struggling_to_rise",
    "motionless",
    "crawling",
]


class PersonState(BaseModel):
    """How a person is at the END of the clip, plus whether they fell during it."""

    fell: bool = False
    posture: Literal["lying", "sitting", "standing", "walking", "unknown"] = "unknown"
    activity: Activity = "unknown"
    on_floor: bool = False
    location_surface: Literal["floor", "sofa", "bed", "unknown"] = "unknown"
    distress_signs: list[DistressSign] = Field(default_factory=list)
    eyes: Literal["open", "closed", "unknown"] = "unknown"
    gesture: Literal["none", "pointing", "shielding_eyes", "waving"] = "none"
    attention_target: str | None = None


class ObjectSighting(BaseModel):
    name: str
    location_desc: str


class SoundRead(BaseModel):
    """What a sound-only Perceptron call returns. The picture is not the point."""

    sounds: list[SoundLabel] = Field(default_factory=list)
    heard: str = ""


class TranscriptRead(BaseModel):
    """Words spoken in the clip. Empty when nobody talks."""

    transcript: str = ""
    clear: bool = False


class SceneState(BaseModel):
    # First on purpose: the model writes what happened, in order, before it
    # fills the end-state fields, so a fall in the middle of a clip is not lost.
    timeline: list[str] = Field(default_factory=list)
    people: list[PersonState] = Field(default_factory=list)
    objects_seen: list[ObjectSighting] = Field(default_factory=list)
    sounds: list[SoundLabel] = Field(default_factory=list)
    text_present: str | None = None
    lighting: Literal["dark", "dim", "normal", "bright"] = "normal"
    summary: str = ""
    confidence: float = 0.0


def scene_json_schema() -> dict:
    """Schema for a Perceptron perception call. That call uses no tools."""
    return SceneState.model_json_schema()


def apply_sound_read(scene: SceneState | None, sound: SoundRead | None) -> SceneState | None:
    """Prefer the dedicated sound call. Fall back to sounds on the scene read."""
    if sound is not None and sound.sounds:
        labels = [label for label in sound.sounds if label != "none"]
        chosen = labels or ["none"]
    elif scene is not None and scene.sounds:
        chosen = list(scene.sounds)
    elif scene is None and sound is None:
        return None
    else:
        chosen = ["none"]
    if scene is None:
        return SceneState(sounds=chosen, summary=sound.heard if sound else "")
    update: dict = {"sounds": chosen}
    if sound is not None and sound.heard and not scene.summary:
        update["summary"] = sound.heard
    return scene.model_copy(update=update)
