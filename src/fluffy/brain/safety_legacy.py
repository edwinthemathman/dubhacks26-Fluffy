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
from fluffy.models.inference import Action, Consent, Evidence, Plan, Trigger, build_plan
from fluffy.models.tool_catalog import validate_args
from fluffy.profile.model import Profile

Phase = Literal["idle", "checkin", "checkin_repeat", "alerted", "watching"]
CHECKIN_TEXT = "Are you okay?"
SECOND_CHECKIN_S = 30
ALERT_S = 60
FOLLOWUP_S = 60
WATCH_S = 5 * 60

_OKAY = {"okay", "ok", "yes", "nod"}
_HELP = {"help", "need help", "shake", "no"}


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

    def on_event(self, kind: str) -> list[SafetyEffect]:
        if kind not in {"possible_fall", "distress_call"}:
            return []
        if self.phase in {"checkin", "checkin_repeat", "alerted"}:
            return []
        if self.phase == "watching" and kind == "possible_fall":
            return []
        self.phase = "checkin"
        self.started = self.clock.now()
        self.event_kind = kind
        self.watch_until = None
        self.next_checkin = None
        return [self._checkin(louder=False, pattern="pulse1")]

    def on_answer(self, raw: str) -> list[SafetyEffect]:
        if self.phase not in {"checkin", "checkin_repeat"}:
            return []
        answer = _classify(raw)
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
        return []

    def tick(self) -> list[SafetyEffect]:
        now = self.clock.now()
        if self.phase in {"checkin", "checkin_repeat"} and self.started is not None:
            elapsed = (now - self.started).total_seconds()
            if self.phase == "checkin" and elapsed >= SECOND_CHECKIN_S:
                self.phase = "checkin_repeat"
                return [self._checkin(louder=True, pattern="strobe")]
            if self.phase == "checkin_repeat" and elapsed >= ALERT_S:
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

    def _checkin(self, *, louder: bool, pattern: str) -> SafetyEffect:
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
                intent=CHECKIN_TEXT,
            ),
            self._tool_plan(
                "show_caption",
                {"text": CHECKIN_TEXT, "size": "xl", "priority": "high"},
                action_class="notify",
                intent=CHECKIN_TEXT,
            ),
        ]
        if speech:
            plans.append(
                self._tool_plan(
                    "announce",
                    {"text": CHECKIN_TEXT, "room": "living_room"},
                    action_class="notify",
                    intent=CHECKIN_TEXT,
                )
            )
        return SafetyEffect(
            kind="checkin",
            text=CHECKIN_TEXT,
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


def _classify(raw: str) -> Literal["okay", "help"] | None:
    text = " ".join(raw.strip().lower().split())
    if text in _OKAY:
        return "okay"
    if text in _HELP:
        return "help"
    return None
