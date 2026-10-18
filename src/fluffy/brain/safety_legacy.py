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

from fluffy.brain.wellbeing import is_okay_phrase, is_request, is_strong_phrase, load_thresholds
from fluffy.clock import Clock
from fluffy.ids import IdFactory
from fluffy.models import Action, Consent, Evidence, Plan, Trigger, build_plan
from fluffy.models.tool_catalog import validate_args
from fluffy.profile.model import Profile

Phase = Literal["idle", "checkin", "checkin_repeat", "alerted", "watching"]
CHECKIN_TEXT = "Are you okay?"
SECOND_CHECKIN_S = 30
ALERT_S = 60
FOLLOWUP_S = 60
PAIN_TEXT_S = 15

_OKAY = {"okay", "ok", "yes", "nod", "i'm okay", "im okay", "i am okay", "i'm fine", "im fine", "i am fine"}
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
        self.incident_id: str | None = None
        self.alerted_once = False
        self.cooldown_until: datetime | None = None
        self.thresholds = load_thresholds()
        self.status = "idle"

    def on_event(self, kind: str, *, source: str | None = None) -> list[SafetyEffect]:
        if kind not in {"possible_fall", "distress_call"}:
            return []
        now = self.clock.now()
        stronger = kind == "distress_call" or source == "sos_button"
        if (
            kind == "possible_fall"
            and self.cooldown_until is not None
            and now < self.cooldown_until
            and not stronger
        ):
            return []
        # A fall that is still going does not start another check-in.
        # A stronger call for help does, even after the first incident alerted.
        if kind == "possible_fall" and self.phase in {"checkin", "checkin_repeat", "alerted", "watching"}:
            return []
        if kind == "distress_call" and self.phase in {"checkin", "checkin_repeat"}:
            return []
        if self.phase in {"idle", "watching"} or self.incident_id is None:
            self.incident_id = self.ids("inc")
            self.alerted_once = False
        self.phase = "checkin"
        self.status = "checking"
        self.started = now
        self.event_kind = kind
        self.watch_until = None
        self.next_checkin = None
        effects = [self._checkin(louder=False, pattern="pulse1")]
        if source == "sos_button":
            effects.extend(self._alert(because="the person pressed SOS"))
        return effects

    def on_answer(self, raw: str) -> list[SafetyEffect]:
        answer = _classify(raw)
        if self.phase == "alerted" and answer == "okay":
            return self._resolve()
        if self.phase not in {"checkin", "checkin_repeat"}:
            return []
        if answer == "okay":
            return self._resolve()
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
            self.status = "idle"
            self.watch_until = None
        return []

    def acknowledge(self) -> list[SafetyEffect]:
        if self.phase != "alerted":
            return []
        self.phase = "idle"
        self.status = "dismissed"
        self.next_checkin = None
        return [SafetyEffect(kind="cleared", text="Caregiver acknowledged.")]

    def stand_down(self) -> list[SafetyEffect]:
        """The incident is over. The next fall or pain can text again."""
        if self.phase == "idle":
            return []
        was_alerted = self.phase == "alerted"
        self.phase = "idle"
        self.status = "resolved_ok" if was_alerted else "dismissed"
        self.started = None
        self.watch_until = None
        self.next_checkin = None
        self.event_kind = None
        return [SafetyEffect(kind="cleared", text="")]

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
        if self.alerted_once:
            return []
        if because == "no response":
            message = f"{person} may have fallen in the living room and has not responded to check-ins."
        elif because == "the person pressed SOS":
            message = f"{person} pressed the help button in the living room."
        else:
            message = f"{person} needs help in the living room"
        told = f"{caregiver} has been notified."
        self.phase = "alerted"
        self.status = "alerted"
        self.alerted_once = True
        self.next_checkin = self.clock.now() + timedelta(seconds=FOLLOWUP_S)
        alert = self._tool_plan(
            "alert_caregiver",
            {
                "severity": "high",
                "message": message,
                "include_snapshot": False,
                "incident_id": self.incident_id or self.ids("inc"),
            },
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
                Evidence(
                    kind="vision",
                    summary="debilitated" if tool == "alert_caregiver" else (self.event_kind or "safety"),
                    at=self.clock.now(),
                )
            ],
        )

    def _resolve(self) -> list[SafetyEffect]:
        person = self.profile.agreements.person_name
        told = self.alerted_once
        self.phase = "watching"
        self.status = "resolved_ok"
        self.cooldown_until = self.clock.now() + timedelta(seconds=self.thresholds.checkin_cooldown_s)
        self.watch_until = self.cooldown_until
        text = f"Update: {person} says they're okay." if told else "Okay. I'll keep an eye on the room."
        return [SafetyEffect(kind="cleared", text=text)]


def _classify(raw: str) -> Literal["okay", "help"] | None:
    text = " ".join(raw.strip().lower().replace("\u2019", "'").split())
    if is_request(text) or is_okay_phrase(text) or text in _OKAY:
        return "okay"
    if is_strong_phrase(text) or text in _HELP:
        return "help"
    if len(text.split()) >= 3:
        return "okay"
    return None
