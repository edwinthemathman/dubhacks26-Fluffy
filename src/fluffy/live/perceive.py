"""Async Perceptron calls on a live window: recent camera frames plus recent audio.

Perceptron has no streaming input. A "live" read is one request over the last
few seconds, sent as timestamped frames (`video_frames`) and, when there is
sound, a separate WAV part (`enable_audio_in_video` does not apply to frames).

The prompts are the clip demo's (`fluffy.demo.analyze`), so prompt fixes there
apply here too.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from perceptron import AsyncClient, SDKError, pydantic_format, video_frames
from perceptron import audio as audio_part

from fluffy.brain.decide import _asked, _asked_choices, _split, system_prompt
from fluffy.demo.analyze import (
    MODEL,
    SOUND_PROMPT,
    scene_prompt,
    tag_prompt,
    _meta_result,
    _parse_model,
    _safe_error,
    _scene_text,
    _usage,
    _user_text,
    transcript_prompt,
)
from fluffy.models.tool_catalog import ParsedCall, perceptron_tools
from fluffy.perception.provider import ToolCall
from fluffy.perception.scene import SceneState, SoundRead, TagRead, TranscriptRead
from fluffy.profile.model import Profile

LIVE_TIMEOUT_S = 30
MAX_ROUNDS = 2

LIVE_NOTE = (
    "This is a live camera. The frames below are the last {seconds:.0f} seconds, "
    "oldest first, and the last frame is right now. 'End of the clip' means now."
)


@dataclass
class Window:
    """A slice of the live feed. Frame timestamps start at 0."""

    frames: list[tuple[bytes, int]]
    wav: bytes | None = None
    seconds: float = 0.0
    ended_at: float = 0.0  # monotonic time of the newest frame

    def media(self, *, with_audio: bool = True) -> list[Any]:
        if len(self.frames) < 2:
            raise ValueError("a live window needs at least two frames")
        parts: list[Any] = [video_frames(list(self.frames))]
        if with_audio and self.wav:
            parts.append(audio_part(self.wav))
        return parts


@dataclass
class Read:
    value: Any = None
    error: str | None = None
    ms: int = 0
    usage: dict[str, int] = field(default_factory=dict)


@dataclass
class Decision:
    reply: str = ""
    asked: str | None = None
    choices: list[str] | None = None
    device: list[ParsedCall] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    rounds: int = 0
    ms: int = 0
    usage: dict[str, int] = field(default_factory=dict)
    error: str | None = None


async def read_scene(
    client: AsyncClient,
    window: Window,
    *,
    effort: str = "none",
    transcript: str = "",
    profile: Profile | None = None,
) -> Read:
    started = time.perf_counter()
    # The frames and the wav go out together, as a recording's picture and
    # soundtrack do. A transcript call here would add a second reasoning pass.
    try:
        completion = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        *window.media(),
                        {
                            "type": "text",
                            "text": (
                                f"{LIVE_NOTE.format(seconds=window.seconds)}\n"
                                f"{scene_prompt(transcript, profile.object_names if profile else None)}"
                            ),
                        },
                    ],
                }
            ],
            response_format=pydantic_format(SceneState, strict=False),
            reasoning_effort=effort,
            max_completion_tokens=1024,
            timeout=LIVE_TIMEOUT_S,
        )
    except SDKError as exc:
        return Read(error=_safe_error(exc), ms=_ms(started))
    scene, error = _parse_model(completion.text, SceneState, "Scene read")
    return Read(value=scene, error=error, ms=_ms(started), usage=_usage(completion))


async def read_tags(client: AsyncClient, window: Window, *, profile: Profile | None = None) -> Read:
    """A slower tag-only look. More frames, low reasoning, no people or sounds."""
    started = time.perf_counter()
    names = profile.object_names if profile is not None else None
    try:
        completion = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        *window.media(),
                        {"type": "text", "text": tag_prompt(names)},
                    ],
                }
            ],
            response_format=pydantic_format(TagRead, strict=False),
            reasoning_effort="low",
            max_completion_tokens=1024,
            timeout=LIVE_TIMEOUT_S,
        )
    except SDKError as exc:
        return Read(error=_safe_error(exc), ms=_ms(started))
    tags, error = _parse_model(completion.text, TagRead, "Room scan")
    return Read(value=tags, error=error, ms=_ms(started), usage=_usage(completion))


async def read_transcript(client: AsyncClient, wav: bytes, profile: Profile | None) -> Read:
    started = time.perf_counter()
    try:
        completion = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        audio_part(wav),
                        {"type": "text", "text": transcript_prompt(profile)},
                    ],
                }
            ],
            response_format=pydantic_format(TranscriptRead, strict=False),
            reasoning_effort="none",
            max_completion_tokens=256,
            timeout=LIVE_TIMEOUT_S,
        )
    except SDKError as exc:
        return Read(error=_safe_error(exc), ms=_ms(started))
    spoken, error = _parse_model(completion.text, TranscriptRead, "Transcript")
    return Read(value=spoken, error=error, ms=_ms(started), usage=_usage(completion))


async def read_sound(client: AsyncClient, wav: bytes) -> Read:
    started = time.perf_counter()
    try:
        completion = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [audio_part(wav), {"type": "text", "text": SOUND_PROMPT}],
                }
            ],
            response_format=pydantic_format(SoundRead, strict=False),
            reasoning_effort="none",
            max_completion_tokens=256,
            timeout=LIVE_TIMEOUT_S,
        )
    except SDKError as exc:
        return Read(error=_safe_error(exc), ms=_ms(started))
    sound, error = _parse_model(completion.text, SoundRead, "Sound read")
    if sound is not None and not [s for s in sound.sounds if s != "none"]:
        # The model often describes the sound ("a doorbell rings") but leaves the
        # label list empty. Fall back to the words it used.
        guessed = labels_from_text(sound.heard)
        if guessed:
            sound = sound.model_copy(update={"sounds": guessed})
    return Read(value=sound, error=error, ms=_ms(started), usage=_usage(completion))


_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("help", ("help", "i fell", "can't get up", "cannot get up")),
    ("pain", ("groan", "moan", "cry", "cries", "crying", "yelp", "whimper", "ouch", " ow", "gasp", "pain")),
    ("impact", ("thud", "crash", "bang", "thump", "slam", "fall", "falls", "fell", "hits the floor")),
    ("doorbell", ("doorbell", "door bell", "ding-dong", "ding dong", "chime", "bell ring")),
    ("knock", ("knock",)),
    ("alarm", ("alarm", "siren", "smoke detector", "beeping", "beeps")),
    ("snoring", ("snore", "snoring")),
    ("door", ("door opens", "door closes", "door creak", "latch", "keys jingle")),
    ("speech", ("speak", "speaks", "says", "said", "talk", "voice", "words")),
]


# These match as whole words. "fall" must not hit "falling asleep", and "help"
# must not hit "helpful". Longer stems ("groan", "knock") still match their endings.
_WHOLE_WORD = {"fall", "falls", "fell", "help", "cry", "said"}


def _mentioned(text: str, word: str) -> bool:
    if word not in _WHOLE_WORD:
        return word in text
    start = 0
    while True:
        found = text.find(word, start)
        if found < 0:
            return False
        before = text[found - 1] if found else " "
        after_at = found + len(word)
        after = text[after_at] if after_at < len(text) else " "
        if not before.isalnum() and not after.isalnum():
            return True
        start = found + 1


def labels_from_text(text: str) -> list[str]:
    """Sound labels named in a one-line description, in priority order."""
    lowered = f" {text.lower()} "
    found = [label for label, words in _KEYWORDS if any(_mentioned(lowered, word) for word in words)]
    if "help" in found and "help me find" in lowered:
        found.remove("help")
    if "doorbell" in found and "door" in found:
        found.remove("door")
    return found


async def answer_from_audio(client: AsyncClient, wav: bytes, question: str) -> Read:
    """While a question is open: yes, no, help, or some other words?"""
    started = time.perf_counter()
    try:
        completion = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        audio_part(wav),
                        {
                            "type": "text",
                            "text": (
                                f'The person was just asked: "{question}". '
                                "From this audio, did they answer yes, no, or ask for help? "
                                "Groaning or crying out with no words counts as help. "
                                "If they said something else, say other. "
                                "If they did not answer, say unclear."
                            ),
                        },
                    ],
                }
            ],
            regex="yes|no|help|other|unclear",
            reasoning_effort="none",
            max_completion_tokens=8,
            timeout=LIVE_TIMEOUT_S,
        )
    except SDKError as exc:
        return Read(error=_safe_error(exc), ms=_ms(started))
    return Read(value=(completion.text or "").strip().lower(), ms=_ms(started), usage=_usage(completion))


async def decide(
    client: AsyncClient,
    window: Window,
    *,
    profile: Profile,
    utterance: str,
    scene: SceneState | None,
    cues: set[str],
    effort: str = "low",
    max_tokens: int = 512,
    asked: str = "",
    answer: str = "",
    room_change: str = "",
    habit_lines: list[str] | None = None,
    tag_lines: list[str] | None = None,
    important: str = "",
    memory: Any = None,
    now: Any = None,
) -> Decision:
    """The decision call with tools. Only device calls from the last round count."""
    started = time.perf_counter()
    # Words already heard. The decision call itself has the wav, so it does not
    # wait on another reasoning pass to transcribe them again.
    transcript = scene.said.strip() if scene is not None and scene.said.strip() else ""
    if not transcript and utterance.strip():
        transcript = utterance.strip()
    user_text = (
        f"{LIVE_NOTE.format(seconds=window.seconds)}\n"
        f"{_user_text(profile, utterance, scene, cues, transcript=transcript, asked=asked, answer=answer, room_change=room_change, habit_lines=habit_lines, tag_lines=tag_lines, important=important)}"
    )
    messages: list[Any] = [
        {"role": "system", "content": system_prompt(profile)},
        {"role": "user", "content": [*window.media(), {"type": "text", "text": user_text}]},
    ]
    result = Decision()
    scene_text = _scene_text(scene)
    for round_index in range(MAX_ROUNDS):
        try:
            completion = await client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=perceptron_tools(),
                parallel_tool_calls=True,
                reasoning_effort=effort,
                max_completion_tokens=max_tokens,
                timeout=LIVE_TIMEOUT_S,
            )
        except SDKError as exc:
            result.error = _safe_error(exc)
            break
        result.rounds = round_index + 1
        result.usage = _usage(completion) or result.usage
        result.reply = (completion.text or "").strip()
        meta, device, bad = _split(
            [
                ToolCall(id=call.id, name=call.name, arguments=call.arguments or "")
                for call in (completion.tool_calls or [])
            ]
        )
        result.rejected.extend(bad)
        asked_now = _asked(meta)
        if asked_now:
            result.asked = asked_now
            result.choices = _asked_choices(meta)
        result.device = device
        if device or not meta or round_index == MAX_ROUNDS - 1 or completion.message is None:
            break
        messages.append(completion.message)
        for call_id, parsed in meta:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": _meta_result(parsed.name, parsed.args, scene_text, memory, now),
                }
            )
    result.ms = _ms(started)
    return result


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
