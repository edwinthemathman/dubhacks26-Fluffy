"""Person profiles. These control how Fluffy talks and how it listens."""

from typing import Literal

from pydantic import BaseModel, Field

ConfirmBack = Literal["low_confidence", "always"]
QuestionStyle = Literal["combined", "one_at_a_time"]


class OutputSettings(BaseModel):
    speech: bool
    captions: bool
    speech_rate: float = 1.0
    light_cues: bool = False
    # When set, a message must always have a caption or a light cue.
    audio_only_forbidden: bool = False


class InputSettings(BaseModel):
    voice: bool = False
    typing: bool = False
    buttons: bool = False
    nod: bool = False
    pointing: bool = False


class QuestionSettings(BaseModel):
    confirm_back: ConfirmBack
    style: QuestionStyle
    repeat_once: bool = False
    repeat_after_s: float = 0


class Agreements(BaseModel):
    notify: bool = True
    safety: bool = True
    caregiver: str
    person_name: str


class Profile(BaseModel):
    id: str
    display_name: str
    needs: list[str] = Field(default_factory=list)
    output: OutputSettings
    input: InputSettings
    questions: QuestionSettings
    agreements: Agreements
    vocabulary: list[str] = Field(default_factory=list)
    important_objects: list[str] = Field(default_factory=list)
    object_names: dict[str, str] = Field(default_factory=dict)

    def delivery_channels(self, *, safety: bool = False) -> list[str]:
        """Channels a message may use.

        Safety check-ins always include a caption and a light, because the
        check-in has to work even when the person cannot hear Fluffy.
        A profile that forbids audio-only delivery never gets speech by itself.
        """
        channels: list[str] = []
        if self.output.captions or safety:
            channels.append("caption")
        if self.output.light_cues or safety:
            channels.append("light")
        speech_ok = self.output.speech
        if self.output.audio_only_forbidden and not channels:
            speech_ok = False
        if speech_ok:
            channels.append("speech")
        return channels
