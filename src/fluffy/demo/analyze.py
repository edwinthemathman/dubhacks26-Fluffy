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
from fluffy.brain.cues import derive
from fluffy.brain.objects import find_line, important_for, object_notices, tag_lines
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
from fluffy.notify.photon import notify_plan
from fluffy.perception.provider import ToolCall
from fluffy.perception.scene import SceneState, SoundRead, TranscriptRead, apply_sound_read
from fluffy.profile.model import Profile
from fluffy.world.model import ObjectMemory, objects_at_end

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
    memory = ObjectMemory()
    if scene is not None:
        scene = scene.model_copy(update={"objects_seen": objects_at_end(scene)})
        memory.note_live(scene.objects_seen, clock.now())
    notices = object_notices(scene, memory, profile, clock.now())
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
        tag_lines=tag_lines(memory, clock.now(), profile),
        important=", ".join(sorted(important_for(profile))),
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
                    "content": _meta_result(parsed.name, parsed.args, scene_text, memory, clock.now()),
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
        trace.append(
            {
                "step": "fallback",
                "summary": "A fall is handled by the safety check-in, which asks if they are okay before anyone is texted.",
                "status": "checking",
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

    notify_plan(plan)

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
        tags=_tag_view(memory, profile, clock.now()),
        notices=_notice_view(notices),
    )


# Prompts are module constants so the live loop (fluffy.live) sends the same words.
SOUND_PROMPT = (
    "Listen to this clip. Label only what you hear. "
    "sounds is a list using only these words: "
    "snoring, door, help, pain, impact, doorbell, knock, alarm, speech, clap, cough, none. "
    "cough is a cough, one or several. A single soft throat-clear, a sneeze, or laughing is not cough. "
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
    "clap means hands clapping, one clap or several. "
    "A clap is never impact, even when it is loud or sharp. "
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
    "what they are on now: floor, sofa, bed, or unknown. "
    "Bending, crouching, stretching, playing, or sitting on the floor on purpose is not a fall.\n"
    "- motion: none, small, or purposeful. purposeful means reaching, waving, or walking.\n"
    "- activity_hint: none, reaching, picking_up, exercising, or playing_on_floor.\n"
    "- fall_confidence: 0 to 1, how sure you are this is a fall rather than a deliberate floor pose.\n"
    "- distress_signs: any of grimacing, holding_body, not_getting_up, "
    "struggling_to_rise, motionless, crawling. Empty if none.\n"
    "- activity: sleeping, arriving, leaving, idle, distressed, fallen, or unknown. "
    "fallen if they fell and are still down. distressed if they look hurt, "
    "scared, or in pain. leaving is only a calm, upright walk out of frame.\n"
    "- gesture: clapping when their hands strike together, once or several times. "
    "stop when an open palm faces the camera, held still at shoulder height or higher "
    "for about a second. A moving hand is waving, not stop. "
    "A wave hello is waving, not stop. "
    "shielding_eyes only when a hand covers their eyes, or they turn their "
    "face sharply away from a lamp and hold it for most of the clip. "
    "A squint, a blink, glasses reflecting light, bright catchlights in the eyes, "
    "or looking at a window or screen is none. "
    "attention_target is a device id such as lamp.living_floor, or null.\n"
    "Lying on the floor is not resting. Someone lying still on a sofa or bed "
    "with eyes closed is resting.\n"
    "sounds: only snoring, door, help, pain, impact, doorbell, knock, alarm, "
    "speech, clap, cough, none. clap is hands clapping, one clap or several, "
    "cough is a cough, one or several. A single soft throat-clear, a sneeze, or laughing is not cough. "
    "including a loud or sharp clap. A clap is never impact. "
    "pain is a groan, cry, or 'ow' and needs no words. "
    "impact is a thud or crash. help is a call for help, "
    "not 'help me find my keys'.\n"
    "said: the words spoken, as close as you can get, including app and song names. "
    "Fill said whenever you hear speech, even if an earlier transcript was empty. "
    "Leave said empty only when nobody speaks.\n"
    "objects_seen: up to 8 small portable things a person might reach for or lose: "
    "keys, phone, wallet, glasses, hearing aids, cane, walker, pill organizer, "
    "remote, cup, water, water bottle, glass, mug, bag, book, charger. "
    "A water bottle, or a glass or cup with water in it, is tagged as water. "
    "Keep cup for other cups. One entry per object, where it is at the END. "
    "If it moved, forget the earlier place: a phone that was in their hand and is then "
    "set on the table is location_desc 'on the table', with_person false. "
    "Do not leave it tagged in their hand. "
    "location_desc is a fixed landmark such as 'on the side table by the sofa'. "
    "Put the left or right part in side, not in location_desc. "
    "side is which part of that surface the object is on, as seen in the picture: "
    "left, right, middle, front, back, or unclear. "
    "with_person is true only when it is still in a hand, pocket, bag, or worn at the end. "
    "on_floor is true when it is on the floor at the end. "
    "near_person is true when it is within arm's reach at the end. "
    "Tag an object even if it is only partly visible.\n"
    "searching is true when they scan surfaces, pat pockets, lift cushions, open drawers, "
    "or retrace steps. Tidying, cooking, and reading a label are not searching. "
    "looking_for is what they want only if they say it or clearly show it, otherwise null.\n"
    "Add a timeline beat when an object changes hands, such as 'puts wallet on table' "
    "or 'walks off without it'.\n"
    "summary: one sentence on how things are at the end.\n"
    "Use unknown when you cannot tell."
)


TAG_PROMPT = (
    "Tag the small portable things you can see in this room, where each one is NOW. "
    "Up to 8 of: keys, phone, wallet, glasses, hearing aids, cane, walker, "
    "pill organizer, remote, cup, water, water bottle, glass, mug, bag, book, charger. "
    "A water bottle, or a glass or cup with water in it, is tagged as water. One entry per object. "
    "If it moved during the clip, tag the place it ended, not where it started. "
    "A phone set on the table is 'on the table', with_person false, even if they were holding it earlier. "
    "location_desc is a fixed landmark. Put left or right in side: "
    "left, right, middle, front, back, or unclear, as seen in the picture. "
    "with_person is true only if it is still in a hand, pocket, bag, or worn. "
    "on_floor is true if it is on the floor now. "
    "near_person is true if it is within arm's reach now. "
    "Tag an object even if it is only partly visible. "
    "Skip people, furniture, and anything fixed to the room."
)


OBSERVE_PROMPT = (
    "actions: only what a hand, head, or body plainly does. Empty is the usual answer. "
    "At most 4, in time order. Each has a count of how many times in this window. "
    "Use unclear instead of guessing. Never say confused, searching, or wants.\n"
    "touch_face_eyes: fingers touch around the eyes, the bridge of the nose, or the temples, then move away. "
    "NOT rubbing tired eyes for more than 2 seconds, adjusting glasses they are wearing, or resting the chin on a hand.\n"
    "touch_head_top: a hand pats or feels the top of the head or hair. NOT smoothing hair in a mirror.\n"
    "pat_chest_pocket: a flat hand pats the shirt chest or chest pocket, 1 to 3 quick taps. "
    "NOT a hand pressed and held on the chest (that is hold_chest), and NOT brushing crumbs.\n"
    "hold_chest: a hand pressed on the chest and kept there 2 seconds or more.\n"
    "pat_pants_pockets: a hand pats or slides into a pants pocket. "
    "NOT pushing up from the thighs to stand, and NOT tucking in a shirt.\n"
    "pat_jacket_or_bag: pats a jacket pocket, or opens and rummages in a bag. "
    "NOT taking a known item straight out and using it.\n"
    "touch_ear: fingers touch or feel behind an ear. NOT cup_ear.\n"
    "cup_ear: a hand cupped behind the ear, turned toward a sound or a person.\n"
    "look_at_surfaces: head and eyes sweep across two or more surfaces. NOT watching TV or following a person or pet.\n"
    "look_down_beside_seat: looks down at the floor beside or under the chair or sofa they are on. "
    "NOT tying shoes or looking at a pet.\n"
    "lift_or_move_things: lifts cushions, papers, or blankets, or opens a drawer, and looks underneath. "
    "NOT tidying that does not stop to look, and NOT cooking.\n"
    "reach_steady: stands or walks while a hand grips furniture, the wall, or a door frame for balance. "
    "NOT a hand resting while they stand still and steady.\n"
    "hold_text_far_or_squint: holds paper or a phone at arm's length or very close, or squints at it. "
    "NOT reading at a normal distance.\n"
    "stop_and_look_around: stops walking and turns the head more than once. NOT stopping because someone spoke.\n"
    "turn_back: starts toward somewhere, then turns around and goes back.\n"
    "press_remote_repeatedly: presses a remote three or more times, or shakes it, while facing the TV. NOT one normal press.\n"
    "touch_throat: one hand rubs the front of the throat, then lets go. "
    "NOT adjusting a collar, scarf, or necklace, NOT scratching, and NOT both hands gripping (that is clutch_throat).\n"
    "cough: a visible cough, a hand or elbow to the mouth with a short jolt of the chest, once or several times. "
    "NOT a yawn, a sneeze, laughing, or a single quiet throat-clear.\n"
    "clutch_throat: one or both hands grip the throat and stay there, often with the mouth open or a distressed face.\n"
    "States, each yes, no, or unclear: wearing_glasses, speaking, on_phone, face_visible. "
    "holding is a list of objects in their hands. "
    "zone is door, sofa, chair, table, kitchen_counter, bed, hallway, other, or unknown. "
    "facing is camera when they face the camera, away, side, or unclear.\n"
    "on_screen is true for a person on a TV, in a photo, poster, or mirror. Ignore those. Pets are not people.\n"
)


def scene_prompt(transcript: str = "", names: dict[str, str] | None = None) -> str:
    """Scene layout. The transcript tells the read what the words were about."""
    words = transcript.strip() or "(not transcribed yet; listen and put the words in said)"
    named = _named_objects(names)
    return (
        f"{SCENE_PROMPT}\n"
        f"{OBSERVE_PROMPT}\n"
        f"{named}"
        f"Transcript: {words}\n"
        "Use the transcript to lay the scene out, not only to label sound. "
        "Add a timeline beat for what they said, placed next to what they were doing. "
        "If they name a portable thing, look for that object and put it in objects_seen "
        "when you can see it, with where it is. "
        "If they say they fell, can't get up, or need help, set fell and activity to fallen or "
        "distressed, unless they are clearly upright and fine. "
        "If they say they just got home, activity can be arriving. "
        "If they say they are tired or going to sleep, and they are lying or sitting, activity can be sleeping. "
        "The picture wins when it plainly contradicts the words. "
        "summary: one sentence that includes what you see and what they said."
    )


def tag_prompt(names: dict[str, str] | None = None) -> str:
    named = _named_objects(names)
    return f"{TAG_PROMPT}\n{named}".rstrip()


def _named_objects(names: dict[str, str] | None) -> str:
    if not names:
        return ""
    bits = ", ".join(f"{key} means {value}" for key, value in names.items())
    return f"This person calls these objects: {bits}. Use those names.\n"


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
    tag_lines: list[str] | None = None,
    important: str = "",
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
    if tag_lines:
        lines.append("- Tagged objects: " + "; ".join(tag_lines))
    if important:
        lines.append(f"- Important to this person: {important}")
    lines.extend(
        [
            f"- Transcript: {words}",
            "The transcript is a decision. If it asks for several things, call every matching tool "
            "in this same turn. It's getting late and they want music means play_sound sound=music "
            "and dim_lights together. "
            "'Help me find my keys' or 'help me get a bottle of water' is a request, not a fall. "
            "Do not call alert_caregiver. You cannot text the caregiver.",
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
            "If someone fell or asked for a bottle of water, do not text anyone. "
            "A request is a request. Falls are handled outside this call. "
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
    tags: list[dict[str, str]] | None = None,
    notices: list[dict[str, Any]] | None = None,
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
        "tags": tags or [],
        "notices": notices or [],
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


def _tag_view(memory: ObjectMemory, profile: Profile, now: Any) -> list[dict[str, str]]:
    return [
        {"name": tag.name, "where": tag.location_desc}
        for tag in memory.tags(now, important_for(profile))
    ]


def _notice_view(notices: list[Any]) -> list[dict[str, Any]]:
    return [
        {"kind": notice.kind, "name": notice.name, "text": notice.text, "choices": list(notice.choices)}
        for notice in notices
    ]


def _meta_result(
    name: str,
    args: dict[str, Any],
    scene_text: str,
    memory: ObjectMemory | None = None,
    now: Any = None,
) -> str:
    if name == "describe_scene":
        return scene_text or "No separate scene read. Use the video."
    if name == "find_object":
        target = str(args.get("name") or "that object")
        if memory is None or now is None:
            return f"There is no saved sighting of {target}. Look in the video."
        return find_line(memory.recall(target, now), now)
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
