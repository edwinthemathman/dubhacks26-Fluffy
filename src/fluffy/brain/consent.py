"""Turns a comfort plan into a question, then into consent or a no.

Only an update with status `approved` may be sent to the executor. Silence
is a no. The speech/cognitive profile hears the question a second time
after 20 seconds, then silence is still a no.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from fluffy.brain.arbiter import LOW_CONFIDENCE, Arbiter
from fluffy.brain.cues import build_help_plan, help_question, judge
from fluffy.brain.habits import HabitBook
from fluffy.clock import Clock
from fluffy.models import Consent, Plan
from fluffy.perception.scene import SceneState
from fluffy.profile.model import Profile

UpdateStatus = Literal["approved", "asking", "repeated", "declined", "dropped", "ignored"]
# Profiles that do not repeat still give up after this long with no answer.
DEFAULT_SILENCE_S = 30.0

_YES = {"yes", "yeah", "yep", "ok", "okay", "sure", "please"}
_NO = {"no", "nope", "nah", "dont", "don't", "cancel", "stop"}


@dataclass
class OpenQuestion:
    question: str
    asked_at: datetime
    profile: Profile
    plan: Plan | None = None
    repeated: bool = False
    # An offer Fluffy raised on its own, not a reply to something just said.
    unprompted: bool = False


@dataclass
class ConsentUpdate:
    status: UpdateStatus
    plan: Plan | None = None
    question: str | None = None
    reason: str = ""

    @property
    def release(self) -> bool:
        return self.status == "approved" and self.plan is not None


class ConsentManager:
    def __init__(self, clock: Clock, arbiter: Arbiter | None = None) -> None:
        self.clock = clock
        self.arbiter = arbiter or Arbiter(clock)
        self.open: OpenQuestion | None = None

    def consider(
        self,
        plan: Plan,
        profile: Profile,
        *,
        transcript_clear: bool,
        scene: SceneState | None = None,
        habits: HabitBook | None = None,
        cues: set[str] | None = None,
    ) -> ConsentUpdate:
        decision = judge(scene, plan, cues)
        if decision == "ask_for_help":
            return self._ask(
                _held_help_plan(plan, profile, self.clock),
                profile,
                question=help_question(profile),
            )

        if (
            self.open is not None
            and profile.questions.style == "one_at_a_time"
            and plan.action_class == "comfort"
            and plan.consent.given_by is None
        ):
            return ConsentUpdate(status="ignored", reason="a question is already open")

        if (
            decision == "act"
            and plan.trigger.kind != "spoken"
            and self.arbiter._declined_recently(plan)
        ):
            return ConsentUpdate(
                status="dropped",
                plan=plan,
                reason="this offer was declined in the last 30 minutes",
            )

        habit_update = self._by_habit(plan, scene, habits, cues)
        if habit_update is not None:
            return habit_update

        plan = _apply_spoken_consent(plan, profile, transcript_clear=transcript_clear)
        if decision == "act" and plan.consent.given_by is None and not _must_ask(plan):
            plan = _apply_cue(plan, self.clock)
        else:
            plan = _apply_preagreed(plan, profile)
        verdict = self.arbiter.review(plan)

        if verdict.status == "allow":
            return self._approved(plan)
        if verdict.status == "considered":
            return ConsentUpdate(status="dropped", plan=plan, reason=verdict.reason)
        if verdict.status == "drop":
            return ConsentUpdate(status="dropped", plan=plan, reason=verdict.reason)
        return self._ask_if_present(plan, profile, cues)

    def _by_habit(
        self,
        plan: Plan,
        scene: SceneState | None,
        habits: HabitBook | None,
        cues: set[str] | None,
    ) -> ConsentUpdate | None:
        """A matching habit is standing consent, down to the habit's own confidence."""
        if habits is None or plan.action_class != "comfort" or plan.trigger.kind == "spoken":
            return None
        if plan.consent.given_by is not None:
            return None
        if self.arbiter._declined_recently(plan):
            return ConsentUpdate(
                status="dropped",
                plan=plan,
                reason="this offer was declined in the last 30 minutes",
            )
        matched = habits.covering(plan, set(cues or ()), scene, self.clock.now())
        if matched is None or plan.confidence < matched.min_confidence:
            return None
        consent = Consent(required=True, given_by="habit", at=self.clock.now())
        plan = plan.model_copy(update={"consent": consent})
        habits.note_used(matched, self.clock.now())
        return self._approved(plan)

    def _ask_if_present(self, plan: Plan, profile: Profile, cues: set[str] | None) -> ConsentUpdate:
        if cues and "left_room" in cues and plan.action_class == "comfort":
            return ConsentUpdate(status="dropped", plan=plan, reason="nobody is in the room")
        return self._ask(plan, profile)

    def answer_voice(self, text: str) -> ConsentUpdate | None:
        return self._answer_yes_no(text, via="voice")

    def answer_typing(self, text: str) -> ConsentUpdate | None:
        return self._answer_yes_no(text, via="typing")

    def answer_button(self, yes: bool) -> ConsentUpdate | None:
        if self.open is None:
            return None
        return self._close(yes, via="button")

    def answer_nod(self, kind: Literal["nod", "shake", "none"]) -> ConsentUpdate | None:
        if self.open is None or kind == "none":
            return None
        return self._close(kind == "nod", via="nod")

    def open_instruction(self, question: str, profile: Profile, *, unprompted: bool) -> None:
        """A question with no plan behind it, from the model's ask_user call."""
        self.open = OpenQuestion(
            question=question,
            asked_at=self.clock.now(),
            profile=profile,
            plan=None,
            unprompted=unprompted,
        )

    def take_open(self) -> tuple[str, Plan | None]:
        """Close the open question without treating it as a yes or a no."""
        question = self.open
        if question is None:
            return "", None
        self.open = None
        return question.question, question.plan

    def answer_pointing(self, device_id: str | None) -> ConsentUpdate | None:
        if self.open is None or self.open.plan is None or not device_id:
            return None
        targets = _targeted_devices(self.open.plan)
        if device_id not in targets:
            return ConsentUpdate(status="ignored", reason="pointed at a different device")
        return self._close(True, via="pointing")

    def tick(self) -> ConsentUpdate | None:
        question = self.open
        if question is None:
            return None
        elapsed = (self.clock.now() - question.asked_at).total_seconds()
        limit = _silence_limit(question)
        if question.profile.questions.repeat_once and not question.repeated and elapsed >= limit:
            question.repeated = True
            question.asked_at = self.clock.now()
            return ConsentUpdate(
                status="repeated",
                plan=question.plan,
                question=question.question,
                reason="repeating the question once",
            )
        if elapsed >= limit:
            return self._close(False, via=None, reason="no answer")
        return None

    def _ask(self, plan: Plan, profile: Profile, question: str | None = None) -> ConsentUpdate:
        question = question or _question_for(plan)
        plan = plan.model_copy(
            update={"consent": Consent(required=True, question=question)}
        )
        self.open = OpenQuestion(
            plan=plan,
            question=question,
            asked_at=self.clock.now(),
            profile=profile,
            unprompted=plan.trigger.kind != "spoken",
        )
        if plan.trigger.kind == "inferred":
            self.arbiter.note_offer()
        return ConsentUpdate(status="asking", plan=plan, question=question)

    def _close(self, yes: bool, *, via: str | None, reason: str = "") -> ConsentUpdate:
        question = self.open
        if question is None:
            return ConsentUpdate(status="ignored", reason="no question is open")
        self.open = None
        if question.plan is None:
            return ConsentUpdate(
                status="declined",
                question=question.question,
                reason=reason or ("answered" if yes else "declined"),
            )
        if not yes:
            self.arbiter.note_decline(question.plan)
            return ConsentUpdate(
                status="declined",
                plan=question.plan,
                question=question.question,
                reason=reason or "declined",
            )
        if via is None:
            raise RuntimeError("a yes answer needs a channel")
        consent = Consent(
            required=True,
            given_by=via,  # type: ignore[arg-type]
            at=self.clock.now(),
            question=question.question,
        )
        plan = question.plan.model_copy(update={"consent": consent})
        return self._approved(plan)

    def _approved(self, plan: Plan) -> ConsentUpdate:
        if plan.action_class == "comfort" and plan.consent.given_by is None:
            raise RuntimeError("comfort plan released without consent")
        return ConsentUpdate(status="approved", plan=plan, reason="ready to send")

    def _answer_yes_no(self, text: str, *, via: Literal["voice", "typing"]) -> ConsentUpdate | None:
        if self.open is None:
            return None
        parsed = classify_yes_no(text)
        if parsed is None:
            return ConsentUpdate(status="ignored", reason="not a yes or no")
        return self._close(parsed, via=via)


def classify_yes_no(text: str) -> bool | None:
    words = [word.strip(".,!?").lower().replace("'", "") for word in text.split()]
    words = [word.replace("'", "") for word in words if word]
    if not words:
        return None
    head = words[:4]
    said_yes = any(word in _YES for word in head)
    said_no = any(word in {item.replace("'", "") for item in _NO} for word in head)
    if said_yes and said_no:
        return None
    if said_yes:
        return True
    if said_no:
        return False
    return None


def _must_ask(plan: Plan) -> bool:
    """A text always waits for a yes, even on a clear spoken request or a cue."""
    return any(action.tool == "send_text" for action in plan.actions)


def _apply_spoken_consent(plan: Plan, profile: Profile, *, transcript_clear: bool) -> Plan:
    if plan.action_class != "comfort" or plan.trigger.kind != "spoken" or _must_ask(plan):
        return plan
    if plan.consent.given_by is not None:
        return plan
    if profile.questions.confirm_back == "always":
        return plan
    if not transcript_clear or plan.confidence < LOW_CONFIDENCE:
        return plan
    if not profile.input.voice:
        return plan
    consent = plan.consent.model_copy(
        update={"required": True, "given_by": "spoken_request", "at": None}
    )
    return plan.model_copy(update={"consent": consent})


def _apply_cue(plan: Plan, clock: Clock) -> Plan:
    consent = Consent(required=True, given_by="cue", at=clock.now())
    return plan.model_copy(update={"consent": consent})


def _held_help_plan(plan: Plan, profile: Profile, clock: Clock) -> Plan:
    """The caregiver call, held until the person says yes.

    A dim-the-lights idea during a fall is not what a yes should approve.
    """
    if plan.actions and all(action.tool == "alert_caregiver" for action in plan.actions):
        return plan.model_copy(
            update={
                "action_class": "safety",
                "intent": f"Call {profile.agreements.caregiver} for help",
                "consent": Consent(required=True),
                "trigger": plan.trigger.model_copy(update={"kind": "safety"}),
            }
        )
    return build_help_plan(clock, profile)


def _apply_preagreed(plan: Plan, profile: Profile) -> Plan:
    if plan.consent.given_by is not None:
        return plan
    if any(action.tool == "alert_caregiver" for action in plan.actions):
        return plan
    if plan.action_class == "notify" and profile.agreements.notify:
        consent = Consent(required=False, given_by="profile")
        return plan.model_copy(update={"consent": consent})
    return plan


def _question_for(plan: Plan) -> str:
    written = _written_question(plan.intent)
    if written:
        return written
    phrases = []
    spoken = f"{plan.intent} {plan.reasoning_summary}"
    for action in sorted(plan.actions, key=lambda item: _ACTION_ORDER.get(item.tool, 50)):
        phrase = _phrase_action(action, spoken)
        if phrase and phrase not in phrases:
            phrases.append(phrase)
    if not phrases:
        return "Should I do that?"
    if len(phrases) == 1:
        return f"Should I {phrases[0]}?"
    if len(phrases) == 2:
        return f"Should I {phrases[0]} and {phrases[1]}?"
    head = ", ".join(phrases[:-1])
    return f"Should I {head}, and {phrases[-1]}?"


_ACTION_ORDER = {
    "dim_lights": 0,
    "set_light_warmth": 1,
    "set_blinds": 2,
    "play_sound": 3,
    "stop_sound": 4,
    "open_door": 8,
    "close_door": 9,
    "play_music": 10,
    "stop_music": 11,
    "send_text": 12,
    "call_person": 13,
    "flash_lights": 5,
    "show_caption": 6,
    "announce": 7,
}


def _phrase_action(action, spoken: str = "") -> str | None:
    tool = action.tool
    args = action.args
    if tool == "dim_lights":
        level = args.get("luminosity")
        if isinstance(level, (int, float)) and level >= 60:
            return "turn the lights on"
        if isinstance(level, (int, float)) and level == 0:
            return "turn the lights off"
        return "dim the lights"
    if tool == "set_light_warmth":
        return f"make the lights {args.get('warmth', 'warm')}"
    if tool == "set_blinds":
        position = args.get("position", 0)
        if isinstance(position, (int, float)) and position >= 50:
            return "open the blinds"
        return "close the blinds"
    if tool == "play_sound":
        sound = str(args.get("sound", "music")).replace("_", " ")
        named = _named_playback(spoken)
        if named:
            return f"put on some {named}"
        if sound == "music":
            return "put on some music"
        return f"play {sound}"
    if tool == "stop_sound":
        return "stop the sound"
    if tool == "flash_lights":
        return "flash the lights"
    if tool == "show_caption":
        return "show a caption"
    if tool == "announce":
        return "say that out loud"
    if tool == "open_door":
        return "open the door"
    if tool == "close_door":
        return "close the door"
    if tool == "play_music":
        query = str(args.get("query") or "").strip()
        return f"play {query}" if query else "play music"
    if tool == "stop_music":
        return "stop the music"
    if tool == "send_text":
        return "send that text"
    if tool == "call_person":
        return "call them"
    return None


def _written_question(text: str) -> str | None:
    """The question the model wrote, when it followed the examples."""
    for line in text.splitlines():
        line = line.strip().strip('"')
        lowered = line.lower()
        if not line.endswith("?"):
            continue
        if lowered.startswith("should i ") or lowered.startswith("do you want"):
            return line
    return None


def _named_playback(text: str) -> str | None:
    """A specific thing they asked to hear, such as Spotify, instead of 'music'."""
    lowered = text.lower()
    for needle, pretty in (
        ("spotify", "Spotify"),
        ("apple music", "Apple Music"),
        ("youtube music", "YouTube Music"),
        ("pandora", "Pandora"),
    ):
        if needle in lowered:
            return pretty
    return None


def _silence_limit(question: OpenQuestion) -> float:
    if question.profile.questions.repeat_once:
        return question.profile.questions.repeat_after_s or DEFAULT_SILENCE_S
    return DEFAULT_SILENCE_S


def _targeted_devices(plan: Plan) -> set[str]:
    found: set[str] = set()
    for action in plan.actions:
        for value in action.args.values():
            if isinstance(value, str) and "." in value:
                found.add(value)
    return found
