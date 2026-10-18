"""SceneState, the structured description Perceptron returns for a frame."""

from typing import Literal

from pydantic import BaseModel, Field


Activity = Literal["sleeping", "arriving", "leaving", "idle", "distressed", "fallen", "unknown"]
# Sound backs up what is seen. It breaks ties between look-alike moments
# (snoring versus speech), and pain or a thud can confirm a fall nobody names.
SoundLabel = Literal[
    "snoring",
    "door",
    "help",
    "pain",
    "impact",
    "doorbell",
    "knock",
    "alarm",
    "speech",
    "clap",
    "cough",
    "none",
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


# What the camera plainly shows a hand, head, or body doing. These labels never
# say what the person wants. Fluffy's own rules (brain/instincts.py) turn a
# pattern of them into a guess, so one scratch of the nose means nothing.
ActionLabel = Literal[
    "touch_face_eyes",
    "touch_head_top",
    "pat_chest_pocket",
    "hold_chest",
    "pat_pants_pockets",
    "pat_jacket_or_bag",
    "touch_ear",
    "cup_ear",
    "look_at_surfaces",
    "look_down_beside_seat",
    "lift_or_move_things",
    "reach_steady",
    "hold_text_far_or_squint",
    "stop_and_look_around",
    "turn_back",
    "press_remote_repeatedly",
    "touch_throat",
    "cough",
    "clutch_throat",
]
Side = Literal["left", "right", "middle", "front", "back", "unclear"]
Facing = Literal["camera", "away", "side", "unclear"]
YesNo = Literal["yes", "no", "unclear"]
Zone = Literal["door", "sofa", "chair", "table", "kitchen_counter", "bed", "hallway", "other", "unknown"]


class PersonAction(BaseModel):
    label: ActionLabel
    count: int = Field(default=1, ge=1, le=9)


class PersonState(BaseModel):
    """How a person is at the END of the clip, plus whether they fell during it."""

    # A person on a TV, in a photo, poster, or mirror. Fluffy drops these.
    on_screen: bool = False
    fell: bool = False
    posture: Literal["lying", "sitting", "standing", "walking", "unknown"] = "unknown"
    activity: Activity = "unknown"
    on_floor: bool = False
    location_surface: Literal["floor", "sofa", "bed", "unknown"] = "unknown"
    distress_signs: list[DistressSign] = Field(default_factory=list)
    eyes: Literal["open", "closed", "unknown"] = "unknown"
    gesture: Literal["none", "pointing", "shielding_eyes", "waving", "clapping", "stop"] = "none"
    attention_target: str | None = None
    searching: bool = False
    looking_for: str | None = None
    # Plain observations for the instinct rules. Empty actions is the usual answer.
    actions: list[PersonAction] = Field(default_factory=list, max_length=4)
    wearing_glasses: YesNo = "unclear"
    holding: list[str] = Field(default_factory=list)
    speaking: YesNo = "unclear"
    on_phone: YesNo = "unclear"
    face_visible: YesNo = "unclear"
    zone: Zone = "unknown"
    facing: Facing = "unclear"


class ObjectSighting(BaseModel):
    name: str
    location_desc: str = Field(
        description=(
            "Where this object is at the END of the clip, after any move. "
            "A phone set down on the table is 'on the table', not 'in their hand'."
        )
    )
    with_person: bool = Field(
        default=False,
        description="True only if it is still in a hand, pocket, bag, or worn at the end. False once they set it down.",
    )
    on_floor: bool = Field(default=False, description="True if it is on the floor at the end.")
    near_person: bool = Field(default=False, description="True if it is within arm's reach at the end.")
    on_head: bool = Field(default=False, description="True only for glasses pushed up on top of the head.")
    side: Side = Field(
        default="unclear",
        description="Which part of the landmark the object is on, as seen in the picture.",
    )


class TagRead(BaseModel):
    """Objects from a slower room scan. No people, no sounds."""

    objects: list[ObjectSighting] = Field(default_factory=list)


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
    # Words spoken in the clip. The scene read fills this when it hears speech,
    # so a missed transcript call does not leave the words blank.
    said: str = ""
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
