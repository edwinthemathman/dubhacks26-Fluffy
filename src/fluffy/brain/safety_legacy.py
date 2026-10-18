"""Fall and distress check-ins. This path does not call a model.

```
possible_fall | distress_call
  check-in "Are you okay?"
    okay / YES / nod  -> cancel, watch for 5 min
    help / NEED HELP / shake / no -> alert the caregiver now
    no answer 30 s -> second check-in, louder, strobe
    no answer 60 s -> alert the caregiver
  tell the person the caregiver was notified
  check in every 60 s until the caregiver acknowledges
```
"""

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from fluffy.clock import Clock
from fluffy.ids import IdFactory
from fluffy.models import Action, Consent, Evidence, Plan, Trigger, build_plan
from fluffy.models.tool_catalog import validate_args
from fluffy.profile.model import Profile

Phase = Literal["idle", "checkin", "checkin_repeat", "alerted", "watching"]
CHECKIN_TEXT = "Are you okay?"
REASK_TEXT = "Are you okay, or do you need help?"
SECOND_CHECKIN_S = 30
ALERT_S = 60
FOLLOWUP_S = 60
WATCH_S = 5 * 60
PAIN_TEXT_S = 15

_OKAY = {"okay", "ok", "yes", "nod", "i'm okay", "im okay", "i am okay", "i'm fine", "im fine", "i am fine"}
_HELP = {"help", "need help", "i need help", "shake"}


class SafetyEffect(BaseModel):
    kind: Literal["checkin", "alert", "notify_person", "cleared"]
    text: str
    speech: bool = False
    caption: bool = False
    light_pattern: str | None = None
    louder: bool = False
    plans: list[Plan] = Field(default_factory=list)


class SafetyMachine:
    def __init__(self, clock: Clock, profile: Profile, ids: IdFactory | None = None) -> None:
        self.clock = clock
        self.profile = profile
        self.ids = ids or IdFactory()
        self.phase: Phase = "idle"
        self.started: datetime | None = None
        self.watch_until: datetime | None = None
        self.next_checkin: datetime | None = None
        self.event_kind: str | None = None
        self.strength = "strong"
        self.evidence_present = True

    def on_event(self, kind: str, *, strength: str = "strong") -> list[SafetyEffect]:
        if kind not in {"possible_fall", "distress_call"}:
            return []
        # A fall that is still going does not start another check-in.
        # Pain or a call for help does, even after the first incident alerted.
        if kind == "possible_fall" and self.phase in {"checkin", "checkin_repeat", "alerted", "watching"}:
            return []
        if kind == "distress_call" and self.phase in {"checkin", "checkin_repeat"}:
            return []
        self.phase = "checkin"
        self.started = self.clock.now()
        self.event_kind = kind
        self.strength = strength if strength in {"strong", "supporting"} else "strong"
        self.evidence_present = True
        self.watch_until = None
        self.next_checkin = None
        return [self._checkin(louder=False, pattern="pulse1", text=CHECKIN_TEXT)]

    def on_answer(self, raw: str) -> list[SafetyEffect]:
        answer = _classify(raw)
        if self.phase == "alerted" and answer == "okay":
            return self.stand_down()
        if self.phase not in {"checkin", "checkin_repeat"}:
            return []
        if answer == "okay":
            self.phase = "watching"
            self.watch_until = self.clock.now() + timedelta(seconds=WATCH_S)
            return [
                SafetyEffect(
                    kind="cleared",
                    text="Okay. I'll keep an eye on the room.",
                )
            ]
        if answer == "help":
            return self._alert(because="the person needs help")
        return [self._checkin(louder=False, pattern="pulse1", text=REASK_TEXT)]

    def tick(self) -> list[SafetyEffect]:
        now = self.clock.now()
        if self.phase in {"checkin", "checkin_repeat"} and self.started is not None:
            elapsed = (now - self.started).total_seconds()
            if self.phase == "checkin" and elapsed >= SECOND_CHECKIN_S:
                self.phase = "checkin_repeat"
                return [self._checkin(louder=True, pattern="strobe")]
            if self.phase == "checkin_repeat" and elapsed >= ALERT_S:
                if self.strength == "supporting" and not self.evidence_present:
                    self.phase = "idle"
                    self.started = None
                    return [SafetyEffect(kind="cleared", text="They got up. I didn't text anyone.")]
                return self._alert(because="no response")
        if self.phase == "alerted" and self.next_checkin is not None and now >= self.next_checkin:
            self.next_checkin = now + timedelta(seconds=FOLLOWUP_S)
            return [self._checkin(louder=False, pattern="pulse1")]
        if self.phase == "watching" and self.watch_until is not None and now >= self.watch_until:
            self.phase = "idle"
            self.watch_until = None
        return []

    def acknowledge(self) -> list[SafetyEffect]:
        if self.phase != "alerted":
            return []
        self.phase = "idle"
        self.next_checkin = None
        return [SafetyEffect(kind="cleared", text="Caregiver acknowledged.")]

    def stand_down(self) -> list[SafetyEffect]:
        """The incident is over. The next fall or pain can text again."""
        if self.phase == "idle":
            return []
        self.phase = "idle"
        self.started = None
        self.watch_until = None
        self.next_checkin = None
        self.event_kind = None
        return [SafetyEffect(kind="cleared", text="")]

    def _checkin(self, *, louder: bool, pattern: str, text: str = CHECKIN_TEXT) -> SafetyEffect:
        speech = self.profile.output.speech
        plans = [
            self._tool_plan(
                "flash_lights",
                {
                    "room": "living_room",
                    "color": "amber",
                    "pattern": pattern,
                    "duration_s": 6 if louder else 3,
                },
                action_class="notify",
                intent=text,
            ),
            self._tool_plan(
                "show_caption",
                {"text": text, "size": "xl", "priority": "high"},
                action_class="notify",
                intent=text,
            ),
        ]
        if speech:
            plans.append(
                self._tool_plan(
                    "announce",
                    {"text": text, "room": "living_room"},
                    action_class="notify",
                    intent=text,
                )
            )
        return SafetyEffect(
            kind="checkin",
            text=text,
            speech=speech,
            caption=True,
            light_pattern=pattern,
            louder=louder,
            plans=plans,
        )

    def _alert(self, *, because: str) -> list[SafetyEffect]:
        person = self.profile.agreements.person_name
        caregiver = self.profile.agreements.caregiver
        if because == "no response":
            message = f"{person} may have fallen in the living room; no response"
        else:
            message = f"{person} needs help in the living room"
        told = f"{caregiver} has been notified."
        self.phase = "alerted"
        self.next_checkin = self.clock.now() + timedelta(seconds=FOLLOWUP_S)
        alert = self._tool_plan(
            "alert_caregiver",
            {"severity": "high", "message": message, "include_snapshot": False},
            action_class="safety",
            intent=message,
        )
        speech = self.profile.output.speech
        notify_plans = [
            self._tool_plan(
                "show_caption",
                {"text": told, "size": "xl", "priority": "high"},
                action_class="notify",
                intent=told,
            )
        ]
        if speech:
            notify_plans.append(
                self._tool_plan(
                    "announce",
                    {"text": told, "room": "living_room"},
                    action_class="notify",
                    intent=told,
                )
            )
        return [
            SafetyEffect(kind="alert", text=message, plans=[alert]),
            SafetyEffect(
                kind="notify_person",
                text=told,
                speech=speech,
                caption=True,
                plans=notify_plans,
            ),
        ]

    def _tool_plan(self, tool: str, args: dict, *, action_class: str, intent: str):
        checked = validate_args(tool, args)
        given_by = "profile"
        required = action_class == "comfort"
        return build_plan(
            clock=self.clock,
            ids=self.ids,
            trigger=Trigger(kind="safety", event=self.event_kind, room="living_room"),
            action_class=action_class,  # type: ignore[arg-type]
            intent=intent,
            actions=[Action(action_id=self.ids("act"), tool=tool, args=checked)],
            confidence=1.0,
            consent=Consent(required=required, given_by=given_by),
            needs=list(self.profile.needs),
            delivered_via=self.profile.delivery_channels(safety=True),
            reasoning_summary=intent,
            evidence=[
                Evidence(kind="vision", summary=self.event_kind, at=self.clock.now())
            ],
        )


def _classify(raw: str) -> Literal["okay", "help", "reask"] | None:
    text = " ".join(raw.strip().lower().replace("’", "'").split())
    if text in _OKAY or "i'm okay" in text or "im okay" in text or "i am okay" in text or "i'm fine" in text or "im fine" in text:
        return "okay"
    if text in _HELP:
        return "help"
    if text == "no":
        return "reask"
    return "reask"
