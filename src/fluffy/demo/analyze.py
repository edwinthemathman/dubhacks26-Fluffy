"""Send one video clip to Perceptron and turn the reply into a plan.

The scene read and the decision call are separate: a request cannot combine
tools with a JSON schema. The clip is attached again on each follow-up
because the API does not remember earlier calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from perceptron import Client, SDKError, pydantic_format, video

from fluffy.brain.consent import ConsentManager
from fluffy.brain.cues import build_help_plan, derive
from fluffy.brain.habits import (
    clip_transition_cues,
    habit_note,
    load_habits,
    room_change_line,
)
from fluffy.brain.decide import (
    _asked,
    _plan_from_devices,
    _split,
    complete_paired_request,
    system_prompt,
)
from fluffy.clock import Clock
from fluffy.ids import IdFactory
from fluffy.models import Plan, Trigger
from fluffy.models.tool_catalog import perceptron_tools
from fluffy.perception.provider import ToolCall
from fluffy.perception.scene import SceneState, SoundRead, TranscriptRead, apply_sound_read
from fluffy.profile.model import Profile

MODEL = "perceptron-mk1.5"
MAX_ROUNDS = 3
REQUEST_TIMEOUT_S = 120
REASONING_CAP = 4000

SAMPLE_CLIP_URL = (
    "https://raw.githubusercontent.com/perceptron-ai-inc/perceptron/"
    "main/cookbook/_shared/assets/capabilities/video-clipping/mj_shot_short.mp4"
)
SAMPLE_CLIP_NAME = "fluffy-sample-shot.mp4"


class AnalyzeError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


def analyze_clip(
    video_path: Path,
    profile: Profile,
    utterance: str,
    *,
    api_key: str,
    clock: Clock | None = None,
) -> dict[str, Any]:
    clock = clock or Clock()
    utterance = utterance.strip()
    with Client(api_key=api_key, provider="perceptron") as client:
        uploaded = client.files.upload(str(video_path), purpose="vision")
        try:
            return _analyze_uploaded(
                client,
                uploaded.id,
                profile,
                utterance,
                clock=clock,
            )
        finally:
            try:
                client.files.delete(uploaded.id)
            except SDKError:
                pass


def _analyze_uploaded(
    client: Client,
    file_id: str,
    profile: Profile,
    utterance: str,
    *,
    clock: Clock,
) -> dict[str, Any]:
    trace: list[dict[str, Any]] = []
    heard, sound_error = _hear(client, file_id)
    trace.append(
        {
            "step": "heard",
            "summary": (heard.heard if heard else "") or (sound_error or ""),
            "labels": [label for label in (heard.sounds if heard else []) if label != "none"],
            "thinking": None,
        }
    )
    spoken, transcript_error = _transcribe(client, file_id, profile)
    transcript = spoken.transcript.strip() if spoken else ""
    trace.append(
        {
            "step": "transcript",
            "summary": transcript or (transcript_error or ""),
            "thinking": None,
        }
    )
    scene, scene_error, scene_thinking = _read_scene(client, file_id, transcript)
    scene = apply_sound_read(scene, heard)
    if not transcript and scene is not None and scene.said.strip():
        transcript = scene.said.strip()
        trace[-1]["summary"] = transcript
    book = _habit_book()
    cues = derive(scene) | clip_transition_cues(scene)
    trace.append(
        {
            "step": "scene",
            "timeline": list(scene.timeline) if scene is not None else [],
            "summary": (scene.summary if scene is not None else "") or (scene_error or ""),
            "thinking": scene_thinking,
        }
    )
    trace.append({"step": "cues", "cues": sorted(cues)})
    scene_text = _scene_text(scene)
    room_change = room_change_line(profile.agreements.person_name, clip_transition_cues(scene))
    applicable = book.matching(cues, scene, clock.now())

    # Always run the decision call. It watches the clip itself, so it can
    # catch what the structured scene read missed. Skipping it whenever the
    # scene read found no cue was the main source of missed actions.
    user_text = _user_text(
        profile,
        utterance,
        scene,
        cues,
        transcript=transcript,
        room_change=room_change,
        habit_lines=book.prompt_lines(applicable, profile),
    )
    messages: list[Any] = [
        {"role": "system", "content": system_prompt(profile)},
        {
            "role": "user",
            "content": [
                video(file_id=file_id),
                {"type": "text", "text": user_text},
            ],
        },
    ]
    tools = perceptron_tools()
    rejected: list[str] = []
    asked: str | None = None
    reply = ""
    device = []
    rounds = 0
    usage: dict[str, int] = {}

    for round_index in range(MAX_ROUNDS):
        completion = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=tools,
            parallel_tool_calls=True,
            reasoning_effort="low",
            max_completion_tokens=1024,
            timeout=REQUEST_TIMEOUT_S,
            vision_config={"enable_audio_in_video": True},
        )
        rounds += 1
        usage = _usage(completion) or usage
        reply = (completion.text or "").strip()
        # Captured before any parse, so a bad tool call still keeps the thinking.
        thinking = _thinking(getattr(completion, "reasoning", None))
        raw_calls = completion.tool_calls or []
        meta, device, bad = _split(
            [
                ToolCall(id=call.id, name=call.name, arguments=call.arguments or "")
                for call in raw_calls
            ]
        )
        trace.append(
            {
                "step": "decide",
                "round": round_index + 1,
                "thinking": thinking,
                "reply": reply,
                "tool_calls": _trace_calls(meta, device),
                "rejected": bad,
            }
        )
        rejected.extend(bad)
        asked = _asked(meta) or asked
        if device or not meta or round_index == MAX_ROUNDS - 1:
            break
        message = completion.message
        if message is None:
            break
        messages.append(message)
        for call_id, parsed in meta:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": _meta_result(parsed.name, parsed.args, scene_text),
                }
            )

    plan = _plan_from_devices(
        device,
        profile=profile,
        trigger=Trigger(
            kind="spoken" if utterance else "inferred",
            utterance=utterance or None,
            room="living_room",
        ),
        snapshot_id="clip",
        reply=reply,
        clock=clock,
        ids=IdFactory(),
    )
    plan = complete_paired_request(plan, f"{transcript}\n{utterance}")
    habit = None
    if plan is None and "fall" in cues:
        plan = build_help_plan(clock, profile)
        trace.append(
            {
                "step": "fallback",
                "summary": "The model proposed nothing, so the fall rule asked about calling for help.",
                "tool": "alert_caregiver",
            }
        )
    elif plan is None and applicable:
        habit = applicable[0]
        plan = book.plan_for(habit, profile=profile, clock=clock, ids=IdFactory(), snapshot_id="clip")
    elif plan is not None:
        habit = book.covering(plan, cues, scene, clock.now())
    if habit is not None:
        trace.append({"step": "habit", "summary": habit_note(habit)})

    consent = None
    if plan is not None:
        update = ConsentManager(clock).consider(
            plan,
            profile,
            transcript_clear=bool(utterance),
            scene=scene,
            habits=book,
            cues=cues,
        )
        consent = {
            "status": update.status,
            "release": update.release,
            "question": update.question,
            "reason": update.reason,
            "given_by": update.plan.consent.given_by if update.plan else None,
        }
        trace.append(
            {
                "step": "consent",
                "status": update.status,
                "reason": update.reason,
                "question": update.question,
            }
        )
        plan = update.plan or plan

    return _result(
        reply=reply or (consent["question"] if consent else "") or asked or "",
        asked=asked,
        scene=scene,
        scene_error=scene_error,
        sound_error=sound_error,
        heard=heard.heard if heard else "",
        transcript=transcript,
        transcript_error=transcript_error,
        plan=plan,
        consent=consent,
        rejected=rejected,
        rounds=rounds,
        usage=usage,
        outcome=_outcome(consent),
        trace=trace,
    )


# Prompts are module constants so the live loop (fluffy.live) sends the same words.
SOUND_PROMPT = (
    "Listen to this clip. Label only what you hear. "
    "sounds is a list using only these words: "
    "snoring, door, help, pain, impact, doorbell, knock, alarm, speech, none. "
    "none means silence. Do not put none next to another label. "
    "help means a call for help, or someone saying they fell, "
    "can't get up, or need help. "
    "'Help me find my keys' is speech, not help. "
    "pain means a groan, moan, cry, yelp, 'ow', 'ah', or a sharp gasp. "
    "Pain needs no words; label it even if nobody says anything. "
    "impact means a thud, crash, bump, or something or someone hitting the floor. "
    "snoring means they sound asleep. "
    "door is only a door opening or closing, a latch, or keys. "
    "A heavy thud of a body or object hitting the floor is impact, not door. "
    "If you are unsure between door and impact, use impact. "
    "After an impact, listen for groans, gasps, or hard breathing and label pain. "
    "doorbell, knock, and alarm are those sounds. "
    "speech is talking that is not a call for help. "
    "A clip can have several labels, e.g. impact and pain. "
    "heard is one short sentence about the soundtrack, in order."
)


def transcript_prompt(profile: Profile | None) -> str:
    words = ", ".join(profile.vocabulary) if profile and profile.vocabulary else "none"
    return (
        "Write down the speech in this clip. Listen to the soundtrack. "
        "transcript is the words that were said, in order, including names of apps, songs, and people. "
        "If someone mentions Spotify, a song, or anything else by name, those words belong in the transcript. "
        "Leave transcript empty only when there is no speech at all. "
        "Do not describe groans, thuds, or music that has no words. "
        "Guess the words when speech is hard to make out. "
        "clear is true only when you are confident those are the words. "
        f"Words this person often uses: {words}."
    )

# Used for both uploaded clips and live frame windows.
SCENE_PROMPT = (
    "You are the eyes of a home companion for someone with a disability. "
    "Watch the whole clip from start to end before answering.\n"
    "timeline: 2 to 6 short beats, in order, of what people DO, "
    "e.g. 'standing by sofa', 'stumbles', 'falls to floor', "
    "'holds knee, grimaces', 'stays down'. Write this first.\n"
    "If a person stumbles, drops low, or goes down toward the floor as they "
    "leave the frame, or vanishes from view suddenly, that is a fall, not leaving. "
    "Write it in the timeline, e.g. 'stumbles', 'drops out of view'. "
    "A thud or crash right after someone moves out of view means they likely fell.\n"
    "people: one entry per person, describing them at the END of the clip. "
    "If they are not visible at the end, describe them as last seen.\n"
    "- fell: true if they went from upright to the floor or ground "
    "at any point, even if nobody says anything, including going down "
    "at the edge of the frame or out of view.\n"
    "- on_floor: true if they are on the floor now. location_surface is "
    "what they are on now: floor, sofa, bed, or unknown.\n"
    "- distress_signs: any of grimacing, holding_body, not_getting_up, "
    "struggling_to_rise, motionless, crawling. Empty if none.\n"
    "- activity: sleeping, arriving, leaving, idle, distressed, fallen, or unknown. "
    "fallen if they fell and are still down. distressed if they look hurt, "
    "scared, or in pain. leaving is only a calm, upright walk out of frame.\n"
    "- gesture: shielding_eyes only when a hand covers their eyes, or they turn their "
    "face sharply away from a lamp and hold it for most of the clip. "
    "A squint, a blink, glasses reflecting light, bright catchlights in the eyes, "
    "or looking at a window or screen is none. "
    "attention_target is a device id such as lamp.living_floor, or null.\n"
    "Lying on the floor is not resting. Someone lying still on a sofa or bed "
    "with eyes closed is resting.\n"
    "sounds: only snoring, door, help, pain, impact, doorbell, knock, alarm, "
    "speech, none. pain is a groan, cry, or 'ow' and needs no words. "
    "impact is a thud or crash. help is a call for help, "
    "not 'help me find my keys'.\n"
    "said: the words spoken, as close as you can get, including app and song names. "
    "Fill said whenever you hear speech, even if an earlier transcript was empty. "
    "Leave said empty only when nobody speaks.\n"
    "objects_seen is only for keys, phone, glasses, wallet, pill organizer, "
    "cane, or hearing aids.\n"
    "summary: one sentence on how things are at the end.\n"
    "Use unknown when you cannot tell."
)


def scene_prompt(transcript: str = "") -> str:
    """Scene layout. The transcript tells the read what the words were about."""
    words = transcript.strip() or "(not transcribed yet; listen and put the words in said)"
    return (
        f"{SCENE_PROMPT}\n"
        f"Transcript: {words}\n"
        "Use the transcript to lay the scene out, not only to label sound. "
        "Add a timeline beat for what they said, placed next to what they were doing. "
        "If they name keys, a phone, glasses, a wallet, a pill organizer, a cane, or hearing aids, "
        "look for that object and put it in objects_seen when you can see it, with where it is. "
        "If they say they fell, can't get up, or need help, set fell and activity to fallen or "
        "distressed, unless they are clearly upright and fine. "
        "If they say they just got home, activity can be arriving. "
        "If they say they are tired or going to sleep, and they are lying or sitting, activity can be sleeping. "
        "The picture wins when it plainly contradicts the words. "
        "summary: one sentence that includes what you see and what they said."
    )


def _transcribe(
    client: Client, file_id: str, profile: Profile
) -> tuple[TranscriptRead | None, str | None]:
    try:
        completion = client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        video(file_id=file_id),
                        {"type": "text", "text": transcript_prompt(profile)},
                    ],
                }
            ],
            response_format=pydantic_format(TranscriptRead, strict=False),
            reasoning_effort="low",
            max_completion_tokens=256,
            timeout=REQUEST_TIMEOUT_S,
            vision_config={"enable_audio_in_video": True},
        )
    except SDKError as exc:
        return None, _safe_error(exc)
    return _parse_model(completion.text, TranscriptRead, "Transcript")


def _hear(client: Client, file_id: str) -> tuple[SoundRead | None, str | None]:
    """A sound-only call. The picture is attached so a voice can be placed."""
    try:
        completion = client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        video(file_id=file_id),
                        {
                            "type": "text",
                            "text": SOUND_PROMPT,
                        },
                    ],
                }
            ],
            response_format=pydantic_format(SoundRead, strict=False),
            reasoning_effort="none",
            max_completion_tokens=256,
            timeout=REQUEST_TIMEOUT_S,
            vision_config={"enable_audio_in_video": True},
        )
    except SDKError as exc:
        return None, _safe_error(exc)
    return _parse_model(completion.text, SoundRead, "Sound read")


def _read_scene(
    client: Client, file_id: str, transcript: str = ""
) -> tuple[SceneState | None, str | None, str | None]:
    try:
        completion = client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        video(file_id=file_id),
                        {
                            "type": "text",
                            "text": scene_prompt(transcript),
                        },
                    ],
                }
            ],
            response_format=pydantic_format(SceneState, strict=False),
            # Following a person through a clip needs a little reasoning; "none"
            # tended to describe one moment and miss a fall in the middle.
            reasoning_effort="low",
            max_completion_tokens=1024,
            timeout=REQUEST_TIMEOUT_S,
            vision_config={"enable_audio_in_video": True},
        )
    except SDKError as exc:
        return None, _safe_error(exc), None
    # Before the JSON parse, so a failed scene still keeps the model's words.
    thinking = _thinking(getattr(completion, "reasoning", None))
    scene, error = _parse_model(completion.text, SceneState, "Scene read")
    return scene, error, thinking


def _parse_model(text: str | None, model: type, label: str):
    raw = (text or "").strip()
    if not raw:
        return None, f"Perceptron returned an empty {label.lower()}."
    try:
        return model.model_validate_json(raw), None
    except ValueError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                return model.model_validate(json.loads(raw[start : end + 1])), None
            except (ValueError, json.JSONDecodeError) as exc:
                return None, f"{label} was not usable JSON ({exc})."
        return None, f"{label} was not JSON."


def _scene_text(scene: SceneState | None) -> str:
    if scene is None:
        return ""
    text = scene.summary
    if scene.timeline:
        text = f"What happened: {' -> '.join(scene.timeline)}. {text}"
    if scene.text_present:
        text = f"{text} Text visible: {scene.text_present}."
    return text.strip()


def _person_line(person) -> str:
    parts = [person.activity, person.posture]
    if person.fell:
        parts.append("FELL during the clip")
    if person.on_floor:
        parts.append(f"on the {person.location_surface if person.location_surface != 'unknown' else 'floor'}")
    elif person.location_surface != "unknown":
        parts.append(f"on the {person.location_surface}")
    if person.distress_signs:
        parts.append("signs: " + ", ".join(person.distress_signs))
    parts.append(f"eyes {person.eyes}")
    if person.gesture != "none":
        parts.append(person.gesture)
    return ", ".join(parts)


def _user_text(
    profile: Profile,
    utterance: str,
    scene: SceneState | None,
    cues: set[str] | None = None,
    transcript: str = "",
    asked: str = "",
    answer: str = "",
    room_change: str = "",
    habit_lines: list[str] | None = None,
) -> str:
    said = utterance or "(nothing typed)"
    words = transcript.strip() or "(none)"
    if scene is None:
        timeline = "not available"
        people = "unknown"
        sounds = "not labeled"
        lighting = "unknown"
    else:
        timeline = " -> ".join(scene.timeline) or "not available"
        people = "; ".join(_person_line(person) for person in scene.people) or "nobody described"
        labels = [label for label in scene.sounds if label != "none"]
        sounds = ", ".join(labels) if labels else "none"
        lighting = scene.lighting
    flagged = ", ".join(sorted(cues)) if cues else "none"
    lines = [
            "The video is the ground truth. Watch it start to end, then decide.",
            "A quick first read of the clip is below. It can miss things or be wrong; "
            "where it disagrees with the video, trust the video.",
            f"- What happened: {timeline}",
            f"- People at the end: {people}",
            f"- Lighting: {lighting}",
            f"- Sounds: {sounds}",
            f"- Cues flagged so far: {flagged}",
    ]
    if room_change:
        lines.append(f"- Room change: {room_change}")
    if habit_lines:
        lines.append("- Habits that apply now:")
        lines.extend(f"- {line}" for line in habit_lines)
    lines.extend(
        [
            f"- Transcript: {words}",
            "The transcript is a decision. If it asks for several things, call every matching tool "
            "in this same turn. It's getting late and they want music means play_sound sound=music "
            "and dim_lights together. "
            "If it says they fell, can't get up, or need help, call alert_caregiver and nothing else. "
            "'Help me find my keys' is a request to find keys, not a fall.",
            f"- Typed: {said}",
            (
                f'Fluffy just asked: "{asked.strip()}". The person answered: "{answer.strip()}".'
                if asked.strip() and answer.strip()
                else ""
            ),
            "",
            f"Room: living_room. Profile: {profile.id}.",
            "Devices: lamp.living_floor (floor lamp by the sofa), light.living_ceiling, "
            "blinds.living_window, speaker.living.",
            "",
            "An impact sound with the person out of view or not clearly okay at the end "
            "is a red flag for a fall, not an arrival or someone leaving.",
            "",
            "Decide now from the picture and the transcript together. "
            'If you call a comfort tool, your reply is the question, such as "Should I put on some Spotify?" '
            "or \"Should I dim the lights and put on music?\". "
            "Use the words they said. Do not describe what you saw. "
            "If the transcript asks for one thing or several, call every tool it needs in this turn. "
            "If someone fell, is down on the floor, or looks hurt, or the transcript says they fell, "
            "can't get up, or need help, or there is a thud and the person is out of view or not "
            "clearly okay, call alert_caregiver and nothing else. The app asks before calling anyone. "
            "If nothing in the picture or the transcript needs a change, call no tools.",
        ]
    )
    return "\n".join(lines)


def _habit_book():
    path = Path(__file__).resolve().parents[3] / "config" / "habits.toml"
    if not path.exists():
        from fluffy.brain.habits import HabitBook

        return HabitBook()
    return load_habits(path)


def _result(
    *,
    reply: str,
    asked: str | None,
    scene: SceneState | None,
    scene_error: str | None,
    sound_error: str | None,
    heard: str,
    transcript: str,
    transcript_error: str | None,
    plan: Plan | None,
    consent: dict[str, Any] | None,
    rejected: list[str],
    rounds: int,
    usage: dict[str, int],
    outcome: str,
    trace: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "reply": reply,
        "asked": asked,
        "outcome": outcome,
        "heard": heard,
        "transcript": transcript,
        "transcript_error": transcript_error,
        "scene": scene.model_dump() if scene is not None else None,
        "scene_error": scene_error,
        "sound_error": sound_error,
        "plan": _plan_view(plan),
        "consent": consent,
        "rejected": rejected,
        "rounds": rounds,
        "usage": usage,
        "trace": trace,
    }


def _thinking(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    if len(text) > REASONING_CAP:
        return text[:REASONING_CAP]
    return text


def _trace_calls(meta: list[tuple[str, Any]], device: list[Any]) -> list[dict[str, Any]]:
    calls = [parsed for _, parsed in meta]
    calls.extend(device)
    return [
        {
            "name": parsed.name,
            "args": parsed.args,
            "reason": parsed.reason,
            "confidence": parsed.confidence,
        }
        for parsed in calls
    ]


def _outcome(consent: dict[str, Any] | None) -> str:
    if consent is None:
        return "quiet"
    if consent.get("release"):
        return "act"
    if consent.get("status") == "asking":
        return "ask"
    return "quiet"


def _meta_result(name: str, args: dict[str, Any], scene_text: str) -> str:
    if name == "describe_scene":
        return scene_text or "No separate scene read. Use the video."
    if name == "find_object":
        target = args.get("name") or "that object"
        return f"There is no saved sighting of {target}. Look in the video."
    if name == "read_text":
        return scene_text or "Read any text you can see in the video."
    if name == "look":
        return "The video is already attached. Answer from it, briefly."
    if name == "ask_user":
        return "The question will be shown to the person. Continue if you still need a device action."
    if name == "confirm_plan":
        return "There is no earlier plan on this page to confirm."
    return "No result."


def _plan_view(plan: Plan | None) -> dict[str, Any] | None:
    if plan is None:
        return None
    return {
        "plan_id": plan.plan_id,
        "action_class": plan.action_class,
        "intent": plan.intent,
        "confidence": plan.confidence,
        "trigger": plan.trigger.kind,
        "actions": [{"tool": action.tool, "args": action.args} for action in plan.actions],
    }


def _usage(completion: Any) -> dict[str, int]:
    usage = getattr(completion, "usage", None)
    if usage is None:
        return {}
    found = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = getattr(usage, key, None)
        if isinstance(value, int):
            found[key] = value
    return found


def _safe_error(exc: SDKError) -> str:
    message = str(exc).strip() or exc.code or "Perceptron request failed"
    return message.replace("\n", " ")
