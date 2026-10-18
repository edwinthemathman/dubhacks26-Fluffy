"""Which cue a scene is, once Perceptron has read the picture and the sound.

Vision leads. What a person does (falls, lies still on the floor, shields
their eyes, dozes off) is the main signal. Sound backs it up and breaks ties
between look-alike moments: snoring versus speech, a door versus silence.
A fall never needs the word "help": falling, staying on the floor, or
looking hurt is enough, and pain or a thud confirms it.
Policy only auto-releases a comfort tool when it matches the cue and the
model is at least 0.7 confident.
"""

from typing import Literal

from fluffy.brain.arbiter import LOW_CONFIDENCE
from fluffy.clock import Clock
from fluffy.ids import IdFactory
from fluffy.models import Action, Consent, Plan, Trigger, build_plan
from fluffy.perception.scene import SceneState
from fluffy.profile.model import Profile

CueDecision = Literal["act", "ask", "ask_for_help", "notify", "quiet"]
Support = Literal["both", "seen", "heard", "none"]
PhysicalEvent = Literal["clap", "fall", "asleep"]

NOTIFY_SOUNDS = frozenset({"doorbell", "knock", "alarm"})
LIGHT_TOOLS = frozenset({"dim_lights", "set_light_warmth"})
DIM_MAX = 40
ON_MIN = 60


_VOLUME_UP = (
    "louder",
    "volume up",
    "turn the volume up",
    "turn the music up",
    "turn it up",
    "bit louder",
    "too quiet",
)
_VOLUME_DOWN = (
    "quieter",
    "softer",
    "volume down",
    "turn the volume down",
    "turn the music down",
    "turn it down",
    "bit quieter",
    "too loud",
)
_VOLUME_NUMBER = (
    "volume to ",
    "volume at ",
    "the volume to ",
    "the volume at ",
    "music to ",
    "music at ",
    "volume ",
)


def volume_request(text: str) -> dict[str, int | str] | None:
    """A spoken volume change. Absolute levels win over up or down."""
    lowered = " ".join(text.lower().replace("%", " ").split())
    if lowered in {"mute", "mute it", "mute the music"}:
        return {"op": "mute"}
    number = _volume_number(lowered)
    if number is not None:
        return {"op": "set", "level": number}
    up = any(phrase in lowered for phrase in _VOLUME_UP)
    down = any(phrase in lowered for phrase in _VOLUME_DOWN)
    if up and not down:
        return {"op": "up"}
    if down and not up:
        return {"op": "down"}
    return None


def _volume_number(lowered: str) -> int | None:
    for stem in _VOLUME_NUMBER:
        at = lowered.find(stem)
        if at < 0:
            continue
        rest = lowered[at + len(stem):].lstrip()
        digits = ""
        for char in rest:
            if char.isdigit():
                digits += char
            else:
                break
        if digits:
            return max(0, min(100, int(digits)))
    return None


def wants_music_stop(text: str) -> bool:
    """True when the words are a request to stop the song, not a longer sentence that mentions stopping."""
    lowered = text.lower().strip(" .!?")
    if lowered in {"stop", "pause"}:
        return True
    return any(
        phrase in lowered
        for phrase in ("stop the music", "stop the song", "pause the music", "pause the song", "turn the music off")
    )


def heard(scene: SceneState | None) -> set[str]:
    if scene is None:
        return set()
    return {label for label in scene.sounds if label != "none"}


def support(scene: SceneState | None, event: PhysicalEvent) -> Support:
    """Whether the picture, the sound, or both back this physical event."""
    if scene is None:
        return "none"
    sounds = heard(scene)
    if event == "clap":
        seen = any(person.gesture == "clapping" for person in scene.people)
        heard_it = bool(sounds & {"clap", "impact"})
    elif event == "fall":
        seen = any(_fall_seen(person) for person in scene.people)
        heard_it = bool(sounds & {"impact", "pain"})
    else:
        seen = any(person.posture in {"lying", "sitting"} for person in scene.people)
        heard_it = "snoring" in sounds
    if seen and heard_it:
        return "both"
    if seen:
        return "seen"
    if heard_it:
        return "heard"
    return "none"


def unidentified_sound(scene: SceneState | None) -> bool:
    """A clap or a thud with no clear picture. It is not an event."""
    if scene is None:
        return False
    if not heard(scene) & {"impact", "clap"}:
        return False
    if not scene.people:
        return True
    if all(person.posture == "unknown" for person in scene.people):
        return True
    return any(person.face_visible == "no" for person in scene.people)


def derive(scene: SceneState | None) -> set[str]:
    """Cue names for this scene. Fall wins over sleep."""
    if scene is None:
        return set()
    sounds = heard(scene)
    cues: set[str] = set()
    if "help" in sounds:
        cues.add("fall")
    for person in scene.people:
        if _looks_hurt(person, sounds):
            cues.add("fall")
        if person.gesture == "shielding_eyes":
            cues.add("glare")
        if person.gesture == "stop":
            cues.add("stop_music")
    if support(scene, "clap") == "both":
        cues.add("clapping")
    if wants_music_stop(scene.said):
        cues.add("stop_music")
    if scene.lighting == "dark":
        cues.add("getting_dark")
    cues.update(sounds & NOTIFY_SOUNDS)
    if "fall" not in cues:
        if _asleep(scene, sounds):
            cues.add("falling_asleep")
        if _arriving(scene, sounds):
            cues.add("arriving")
    return cues


def judge(
    scene: SceneState | None,
    plan: Plan | None,
    cues: set[str] | None = None,
) -> CueDecision:
    """What to do with this scene and the tool the model chose."""
    cues = derive(scene) | set(cues or ())
    if "fall" in cues:
        return "ask_for_help"
    if plan is None:
        return "quiet"
    if any(action.tool == "alert_caregiver" for action in plan.actions):
        return "ask_for_help"
    if plan.action_class == "notify":
        return "notify"
    if plan.action_class == "safety":
        return "ask_for_help"
    if plan.action_class == "comfort":
        if plan.confidence >= LOW_CONFIDENCE and _matches(cues, plan):
            return "act"
        return "ask"
    return "ask"


def help_question(profile: Profile) -> str:
    return f"Should I call {profile.agreements.caregiver} for help?"


def build_help_plan(clock: Clock, profile: Profile) -> Plan:
    caregiver = profile.agreements.caregiver
    return build_plan(
        clock=clock,
        ids=IdFactory(),
        trigger=Trigger(kind="safety", room="living_room"),
        action_class="safety",
        intent=f"Call {caregiver} for help",
        actions=[
            Action(
                action_id="act_help",
                tool="alert_caregiver",
                args={
                    "severity": "high",
                    "message": "Possible fall",
                    "include_snapshot": True,
                },
            )
        ],
        confidence=0.9,
        consent=Consent(required=True),
        needs=list(profile.needs),
        delivered_via=profile.delivery_channels(safety=True),
        reasoning_summary="A fall or a call for help was seen or heard.",
    )


def cap_confidence(plan: Plan | None, cues: set[str], scene: SceneState | None) -> Plan | None:
    """A heard-only physical event cannot be a confident action.

    A spoken request, and a doorbell, knock, or alarm, keep the model's number.
    A spoken request the model scored timidly is lifted into the certain band,
    because the words were the request.
    """
    if plan is None:
        return None
    if plan.trigger.kind == "spoken":
        if 0.4 <= plan.confidence < 0.85:
            return plan.model_copy(update={"confidence": 0.85})
        return plan
    if plan.action_class == "notify":
        return plan
    event = _physical_cue(cues)
    heard_only = event is not None and support(scene, event) == "heard"
    if not heard_only and not unidentified_sound(scene):
        return plan
    if plan.confidence <= 0.3:
        return plan
    return plan.model_copy(update={"confidence": 0.3})


def _physical_cue(cues: set[str]) -> PhysicalEvent | None:
    if "clapping" in cues:
        return "clap"
    if "fall" in cues:
        return "fall"
    if "falling_asleep" in cues:
        return "asleep"
    return None


def _fall_seen(person) -> bool:
    off_furniture = person.location_surface == "floor"
    if person.fell or person.activity in {"distressed", "fallen"}:
        return True
    return bool(person.on_floor and off_furniture)


def _looks_hurt(person, sounds: set[str]) -> bool:
    """A fall or injury, from the picture first. Words are not required."""
    # An unknown surface is not evidence that they left the sofa or the bed.
    off_furniture = person.location_surface == "floor"
    if person.fell or person.activity in {"distressed", "fallen"}:
        return True
    if person.on_floor and off_furniture:
        return True
    down = person.on_floor or (person.posture == "lying" and off_furniture)
    if down and (person.distress_signs or sounds & {"pain", "impact"}):
        return True
    # Hurt but still upright (grimacing, holding a knee) only counts with
    # a pain sound too, so a stretch or a yawn is not a fall.
    signs = set(person.distress_signs) - {"motionless"}
    return bool(signs) and "pain" in sounds


def _asleep(scene: SceneState, sounds: set[str]) -> bool:
    # Speech is the difference between resting and being awake.
    if "speech" in sounds:
        return False
    if sounds & {"door", "doorbell", "knock", "alarm", "pain", "impact"}:
        return False
    resting = any(person.posture in {"lying", "sitting"} for person in scene.people)
    if "snoring" in sounds:
        return resting
    return any(_looks_asleep(person) for person in scene.people)


def _looks_asleep(person) -> bool:
    if person.on_floor and person.location_surface not in {"sofa", "bed"}:
        return False
    if person.fell or person.distress_signs:
        return False
    if person.activity == "sleeping":
        return True
    return (
        person.eyes == "closed"
        and person.posture in {"lying", "sitting"}
        and person.location_surface != "floor"
    )


def _arriving(scene: SceneState, sounds: set[str]) -> bool:
    if any(person.activity == "arriving" for person in scene.people):
        return True
    # A door, or speech while walking into a dark room, is an arrival.
    # Walking in the dark with no sound is not.
    if "door" in sounds and scene.people:
        return True
    if "speech" not in sounds or scene.lighting not in {"dark", "dim"}:
        return False
    return any(person.posture == "walking" for person in scene.people)


def _matches(cues: set[str], plan: Plan) -> bool:
    if not plan.actions:
        return False
    if any(action.tool not in LIGHT_TOOLS for action in plan.actions):
        return False
    level = next(
        (
            action.args.get("luminosity")
            for action in plan.actions
            if action.tool == "dim_lights"
        ),
        None,
    )
    if not isinstance(level, (int, float)):
        return False
    wanted: set[str] = set()
    if "falling_asleep" in cues or "glare" in cues:
        wanted.add("dim")
    if "arriving" in cues or "entered_room" in cues or "getting_dark" in cues:
        wanted.add("on")
    if wanted == {"dim", "on"}:
        return False
    if "dim" in wanted and level <= DIM_MAX:
        return True
    if "on" in wanted and level >= ON_MIN:
        return True
    return False
