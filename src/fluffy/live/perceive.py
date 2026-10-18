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
from fluffy.brain.instincts import place_phrase, speakable
from fluffy.live.perception_log import record as _record
from fluffy.demo.analyze import (
    MODEL,
    SOUND_PROMPT,
    scene_prompt,
    tag_prompt,
    _meta_result,
    _parse_model,
    _safe_error,
    _scene_text,
    _thinking,
    _usage,
    _user_text,
    transcript_prompt,
)
from fluffy.models.tool_catalog import ParsedCall, perceptron_tools
from fluffy.perception.provider import ToolCall
from fluffy.perception.scene import SceneState, SoundRead, TagRead, TranscriptRead
from fluffy.profile.model import Profile

LIVE_TIMEOUT_S = 30
LIVE_DECIDE_ROUNDS = 2
_VISUAL_ONLY_TOOLS = {"describe_scene", "find_object", "read_text", "look"}

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
    thinking: str | None = None


@dataclass
class Located:
    """Where one named object is, from memory or from a targeted look."""

    name: str
    seen: bool
    sentence: str
    surface: str = ""
    side: str = "unclear"
    from_memory: bool = False


@dataclass
class Decision:
    reply: str = ""
    asked: str | None = None
    choices: list[str] | None = None
    device: list[ParsedCall] = field(default_factory=list)
    meta: list[ParsedCall] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    rounds: int = 0
    ms: int = 0
    usage: dict[str, int] = field(default_factory=dict)
    error: str | None = None
    thinking: str | None = None
    confidence: float | None = None


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
                        *window.media(with_audio=window.wav is not None),
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
            max_completion_tokens=2048,
            timeout=LIVE_TIMEOUT_S,
        )
    except SDKError as exc:
        return Read(error=_safe_error(exc), ms=_ms(started))
    scene, error = _parse_model(completion.text, SceneState, "Scene read")
    read = Read(value=scene, error=error, ms=_ms(started), usage=_usage(completion), thinking=_thinking(getattr(completion, "reasoning", None)))
    _log_call("scene", window, completion.text, _scene_parsed(scene), [], read.ms)
    return read


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
                        *window.media(with_audio=False),
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
    if sound is not None:
        guessed = labels_from_text(sound.heard)
        labeled = [s for s in sound.sounds if s != "none"]
        if not labeled and guessed:
            labeled = guessed
        if "clap" in guessed or "clap" in labeled:
            labeled = [s for s in labeled if s != "impact"]
            if "clap" not in labeled:
                labeled.append("clap")
        if labeled != [s for s in sound.sounds if s != "none"]:
            sound = sound.model_copy(update={"sounds": labeled or ["none"]})
    return Read(value=sound, error=error, ms=_ms(started), usage=_usage(completion))


_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("clap", ("clap",)),
    ("help", ("help", "i fell", "can't get up", "cannot get up")),
    ("pain", ("groan", "moan", "cry", "cries", "crying", "yelp", "whimper", "ouch", " ow", "gasp", "pain")),
    ("impact", ("thud", "crash", "bang", "thump", "slam", "fall", "falls", "fell", "hits the floor")),
    ("doorbell", ("doorbell", "door bell", "ding-dong", "ding dong", "chime", "bell ring")),
    ("knock", ("knock",)),
    ("alarm", ("alarm", "siren", "smoke detector", "beeping", "beeps")),
    ("snoring", ("snore", "snoring")),
    ("door", ("door opens", "door closes", "door creak", "latch", "keys jingle")),
    ("cough", ("cough", "coughs", "coughing")),
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
    if "clap" in found and "impact" in found:
        found.remove("impact")
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


async def ask_frames(client: AsyncClient, window: Window, question: str, pattern: str) -> Read:
    """One constrained question about the latest frames. The answer matches `pattern`."""
    started = time.perf_counter()
    try:
        completion = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        *window.media(with_audio=False),
                        {
                            "type": "text",
                            "text": (
                                f"{LIVE_NOTE.format(seconds=window.seconds)}\n"
                                f"{question}\n"
                                "Answer with one choice from the allowed words and nothing else."
                            ),
                        },
                    ],
                }
            ],
            regex=pattern,
            reasoning_effort="none",
            max_completion_tokens=16,
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
    evidence_mode: str = "",
) -> Decision:
    """Up to two calls. A look or a find can come back before the device tools."""
    started = time.perf_counter()
    mode = evidence_mode or "video"
    picture = window
    if mode == "video_plus_transcript":
        picture = Window(frames=list(window.frames), wav=None, seconds=window.seconds, ended_at=window.ended_at)
    transcript = scene.said.strip() if scene is not None and scene.said.strip() else ""
    if not transcript and utterance.strip():
        transcript = utterance.strip()
    preface = ""
    if mode == "video_plus_transcript":
        preface = (
            "Evidence mode: video_plus_transcript.\n"
            "The words below are a finalized browser transcript. No audio is attached.\n"
            "Do not invent speech beyond that text.\n"
        )
    user_text = (
        f"{preface}"
        f"{LIVE_NOTE.format(seconds=picture.seconds)}\n"
        f"{_user_text(profile, utterance, scene, cues, transcript=transcript, asked=asked, answer=answer, room_change=room_change, habit_lines=habit_lines, tag_lines=tag_lines, important=important)}"
    )
    messages: list[Any] = [
        {"role": "system", "content": system_prompt(profile)},
        {"role": "user", "content": [*picture.media(with_audio=picture.wav is not None), {"type": "text", "text": user_text}]},
    ]
    result = await _once(
        client,
        messages,
        perceptron_tools(),
        effort,
        max_tokens,
        rounds=LIVE_DECIDE_ROUNDS,
        scene_text=_scene_text(scene),
        memory=memory,
        now=now,
    )
    result.ms = _ms(started)
    return result


async def decide_text(
    client: AsyncClient,
    *,
    profile: Profile,
    utterance: str,
    effort: str = "low",
) -> Decision:
    """Decide from words only. Evidence mode is transcript_only: no frames and no audio."""

    started = time.perf_counter()
    user_text = "\n".join(
        [
            "Evidence mode: transcript_only.",
            "No current visual evidence was provided. Do not claim what the room looks like.",
            "If the request needs the camera, say so and do not call a device tool.",
            "Answer in one response. Confidence below 0.7 should ask instead of acting.",
            f"Words: {utterance.strip()}",
            f"Profile: {profile.id}. Room: living_room.",
        ]
    )
    messages: list[Any] = [
        {"role": "system", "content": system_prompt(profile)},
        {"role": "user", "content": user_text},
    ]
    tools = [tool for tool in perceptron_tools() if tool["function"]["name"] not in _VISUAL_ONLY_TOOLS]
    result = await _once(client, messages, tools, effort, 512)
    result.ms = _ms(started)
    return result


async def _once(
    client: AsyncClient,
    messages: list[Any],
    tools: list[dict],
    effort: str,
    max_tokens: int,
    *,
    rounds: int = 1,
    scene_text: str = "",
    memory: Any = None,
    now: Any = None,
) -> Decision:
    """One completion, then a second when the first call only looked something up."""

    started = time.perf_counter()
    result = Decision()
    for round_index in range(rounds):
        try:
            completion = await client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=tools,
                parallel_tool_calls=True,
                reasoning_effort=effort,
                max_completion_tokens=max_tokens,
                timeout=LIVE_TIMEOUT_S,
            )
        except SDKError as exc:
            result.error = _safe_error(exc)
            return result
        result.rounds = round_index + 1
        result.usage = _usage(completion) or result.usage
        result.reply = (completion.text or "").strip() or result.reply
        result.thinking = _remember_thinking(result.thinking, getattr(completion, "reasoning", None))
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
        kept = [call for _, call in meta if call.name not in {"ask_user", "confirm_plan"}]
        if kept:
            result.meta = kept
        if device:
            result.device = device
            result.meta = []
            result.confidence = min(call.confidence for call in device)
        calls = [{"name": call.name, "args": call.args, "kept": True} for call in [*kept, *device]]
        calls.extend({"name": item, "args": {}, "kept": False, "why": "rejected"} for item in bad)
        _log_call("decide", None, completion.text, {}, calls, _ms(started))
        if device or not meta or round_index == rounds - 1:
            break
        message = getattr(completion, "message", None)
        if message is None:
            break
        messages.append(message)
        for call_id, parsed in meta:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": _meta_result(parsed.name, parsed.args, scene_text, memory, now),
                }
            )
    return result


async def locate(client: AsyncClient, window: Window, name: str, *, memory: Any = None, now: Any = None) -> Located:
    """One object, not the whole scene. Memory answers if it was seen in the last 2 minutes."""

    from datetime import timedelta

    label = name.strip() or "that"
    if memory is not None and now is not None:
        recall = memory.recall(label, now)
        if (
            recall.status == "fresh"
            and recall.last_seen_at is not None
            and now - recall.last_seen_at <= timedelta(minutes=2)
            and recall.location_desc
        ):
            sentence = speakable(f"Your {label} are {place_phrase(recall.location_desc, recall.side, 'unclear')}.")
            return Located(label, True, sentence, recall.location_desc, recall.side, from_memory=True)
    visible = await ask_frames(client, window, f"Is a {label} visible in any of these frames?", "yes|no|unclear")
    if visible.value != "yes":
        return Located(label, False, _miss_line(label, memory, now))
    side_read = await ask_frames(
        client,
        window,
        f"In any of these frames, which side of the picture is the {label} on?",
        "left|middle|right|unclear",
    )
    surface_read = await ask_frames(
        client,
        window,
        f"In any of these frames, what is the {label} resting on?",
        "side table|counter|coffee table|sofa|floor|hand|none",
    )
    side = side_read.value if side_read.value in {"left", "middle", "right"} else "unclear"
    surface = surface_read.value if surface_read.value and surface_read.value != "none" else ""
    if surface == "hand":
        place = "in their hand"
        held = True
    elif surface:
        place = f"on the {surface}" if not surface.startswith("on ") else surface
        held = False
    else:
        place = "in the room"
        held = False
    if memory is not None and now is not None:
        from fluffy.perception.scene import ObjectSighting

        memory.note_live(
            [ObjectSighting(name=label, location_desc=place, side=side if side != "unclear" else "unclear", with_person=held)],
            now,
        )
    where = "in their hand" if held else place_phrase(place, side, "unclear")
    sentence = speakable(f"Your {label} are {where}.")
    _log_call("locate", window, visible.value, {"name": label, "surface": surface, "side": side}, [], visible.ms)
    return Located(label, True, sentence, place, side)


def _miss_line(name: str, memory: Any, now: Any) -> str:
    if memory is None or now is None:
        return speakable(f"I can't see your {name} from here. Could you look around the sofa?")
    recall = memory.recall(name, now)
    if recall.status == "unknown" or not recall.location_desc:
        return speakable(f"I can't see your {name} from here. Could you look around the sofa?")
    age = 0
    if recall.last_seen_at is not None:
        age = max(1, int((now - recall.last_seen_at).total_seconds() // 60))
    return speakable(
        f"I don't see your {name} right now. I last saw them {recall.location_desc} about {age} minutes ago."
    )


def _scene_parsed(scene: Any) -> dict[str, Any]:
    if scene is None:
        return {}
    return {
        "people": len(getattr(scene, "people", []) or []),
        "objects": [item.name for item in getattr(scene, "objects_seen", []) or []],
        "sounds": list(getattr(scene, "sounds", []) or []),
    }


def _log_call(lane: str, window: Window | None, raw: str | None, parsed: dict[str, Any], calls: list[dict[str, Any]], ms: int) -> None:
    sent = {"frames": 0, "seconds": 0, "audio": False}
    if window is not None:
        sent = {"frames": len(window.frames), "seconds": window.seconds, "audio": window.wav is not None}
    _record(lane=lane, sent=sent, raw=raw, parsed=parsed, calls=calls, ms=ms)


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _remember_thinking(current: str | None, reasoning: str | None) -> str | None:
    thought = _thinking(reasoning)
    if not thought:
        return current
    if not current:
        return thought
    combined = f"{current}\n\n{thought}"
    return combined[:4000]
