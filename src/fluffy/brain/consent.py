"""Turns a comfort plan into a question, then into consent or a no.

Only an update with status `approved` may be sent to the executor. Silence
is a no. The speech/cognitive profile hears the question a second time
after 20 seconds, then silence is still a no.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from fluffy.brain.arbiter import LOW_CONFIDENCE, Arbiter
from fluffy.clock import Clock
from fluffy.models import Consent, Plan
from fluffy.profile.model import Profile

UpdateStatus = Literal["approved", "asking", "repeated", "declined", "dropped", "ignored"]
# Profiles that do not repeat still give up after this long with no answer.
DEFAULT_SILENCE_S = 30.0

_YES = {"yes", "yeah", "yep", "ok", "okay", "sure", "please"}
_NO = {"no", "nope", "nah", "dont", "don't", "cancel", "stop"}


@dataclass
class OpenQuestion:
    plan: Plan
    question: str
    asked_at: datetime
    profile: Profile
    repeated: bool = False


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
    ) -> ConsentUpdate:
        if (
            self.open is not None
            and profile.questions.style == "one_at_a_time"
            and plan.action_class == "comfort"
            and plan.consent.given_by is None
        ):
            return ConsentUpdate(status="ignored", reason="a question is already open")

        plan = _apply_preagreed(plan, profile)
        plan = _apply_spoken_consent(plan, profile, transcript_clear=transcript_clear)
        verdict = self.arbiter.review(plan)

        if verdict.status == "allow":
            return self._approved(plan)
        if verdict.status == "considered":
            return ConsentUpdate(status="dropped", plan=plan, reason=verdict.reason)
        if verdict.status == "drop":
            return ConsentUpdate(status="dropped", plan=plan, reason=verdict.reason)
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

    def answer_pointing(self, device_id: str | None) -> ConsentUpdate | None:
        if self.open is None or not device_id:
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

    def _ask(self, plan: Plan, profile: Profile) -> ConsentUpdate:
        question = _question_for(plan)
        plan = plan.model_copy(
            update={"consent": Consent(required=True, question=question)}
        )
        self.open = OpenQuestion(
            plan=plan,
            question=question,
            asked_at=self.clock.now(),
            profile=profile,
        )
        if plan.trigger.kind == "inferred":
            self.arbiter.note_offer()
        return ConsentUpdate(status="asking", plan=plan, question=question)

    def _close(self, yes: bool, *, via: str | None, reason: str = "") -> ConsentUpdate:
        question = self.open
        if question is None:
            return ConsentUpdate(status="ignored", reason="no question is open")
        self.open = None
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


def _apply_spoken_consent(plan: Plan, profile: Profile, *, transcript_clear: bool) -> Plan:
    if plan.action_class != "comfort" or plan.trigger.kind != "spoken":
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


def _apply_preagreed(plan: Plan, profile: Profile) -> Plan:
    if plan.consent.given_by is not None:
        return plan
    allowed = (
        plan.action_class == "notify"
        and profile.agreements.notify
        or plan.action_class == "safety"
        and profile.agreements.safety
    )
    if not allowed:
        return plan
    consent = Consent(required=False, given_by="profile")
    return plan.model_copy(update={"consent": consent})


def _question_for(plan: Plan) -> str:
    intent = plan.intent.strip()
    if intent.endswith("?"):
        return intent
    if intent:
        return f"{intent}?"
    return "Should I do that?"


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
