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

NOTIFY_SOUNDS = frozenset({"doorbell", "knock", "alarm"})
LIGHT_TOOLS = frozenset({"dim_lights", "set_light_warmth"})
DIM_MAX = 40
ON_MIN = 60


def heard(scene: SceneState | None) -> set[str]:
    if scene is None:
        return set()
    return {label for label in scene.sounds if label != "none"}


def derive(scene: SceneState | None) -> set[str]:
    """Cue names for this scene. Fall wins over sleep."""
    if scene is None:
        return set()
    sounds = heard(scene)
    cues: set[str] = set()
    if "help" in sounds:
        cues.add("fall")
    if _thud_out_of_sight(scene, sounds):
        cues.add("fall")
    for person in scene.people:
        if _looks_hurt(person, sounds):
            cues.add("fall")
        if person.gesture == "shielding_eyes":
            cues.add("glare")
    if scene.lighting == "dark":
        cues.add("getting_dark")
    cues.update(sounds & NOTIFY_SOUNDS)
    if "fall" not in cues:
        if _asleep(scene, sounds):
            cues.add("falling_asleep")
        if _arriving(scene, sounds):
            cues.add("arriving")
    return cues


def judge(scene: SceneState | None, plan: Plan | None) -> CueDecision:
    """What to do with this scene and the tool the model chose."""
    cues = derive(scene)
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


def _looks_hurt(person, sounds: set[str]) -> bool:
    """A fall or injury, from the picture first. Words are not required."""
    off_furniture = person.location_surface not in {"sofa", "bed"}
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


def _thud_out_of_sight(scene: SceneState, sounds: set[str]) -> bool:
    """A thud nobody can be seen to be fine after, e.g. a fall at the frame edge.

    The app asks before calling anyone, so a false alarm only costs a question.
    """
    if "impact" not in sounds:
        return False
    if "pain" in sounds:
        return True
    if not scene.people:
        return True
    return all(
        person.posture == "unknown" or person.activity == "leaving"
        for person in scene.people
    )


def _asleep(scene: SceneState, sounds: set[str]) -> bool:
    # Speech is the difference between resting and being awake.
    if "speech" in sounds:
        return False
    if "snoring" in sounds:
        return True
    if sounds & {"door", "doorbell", "knock", "alarm", "pain", "impact"}:
        return False
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
    if "arriving" in cues or "getting_dark" in cues:
        wanted.add("on")
    if wanted == {"dim", "on"}:
        return False
    if "dim" in wanted and level <= DIM_MAX:
        return True
    if "on" in wanted and level >= ON_MIN:
        return True
    return False
