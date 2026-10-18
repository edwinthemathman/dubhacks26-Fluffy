"""Deterministic two-check-in incident manager.

Perception stays upstream. This machine only times questions and decides when
one caregiver alert becomes eligible. It does not diagnose a fall.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from fluffy.config import HomeConfig
from fluffy.ids import new_id
from fluffy.models.contracts import PossibleFallEvent


_NOT_OKAY = re.compile(r"\bnot\s+ok(ay)?\b")
_OKAY = re.compile(r"^(please\s+)?(i'm okay|im okay|i am okay|i'm ok|im ok|i am ok|okay)(\s+please)?$")
_HELP = re.compile(r"^(please\s+)?(help me|i need help|help)(\s+please)?$")


def classify_reply(text: str) -> str:
    normalized = " ".join(text.strip().lower().replace("\u2019", "'").replace(".", "").split())
    if _NOT_OKAY.search(normalized):
        return "ambiguous"
    if _OKAY.fullmatch(normalized):
        return "okay"
    if _HELP.fullmatch(normalized):
        return "help"
    return "ambiguous"


@dataclass
class SafetyCommand:
    kind: str
    incident_id: str
    text: str = ""
    message_id: str = ""
    question_id: str = ""
    severity: str = "high"
    action_id: str = ""


@dataclass
class Incident:
    incident_id: str
    session_id: str
    room: str
    phase: str
    opened_at: datetime
    source: str
    audio_failed: bool = False
    question_id: str | None = None
    question_text: str | None = None
    message_id: str | None = None
    window_deadline: datetime | None = None
    speak_deadline: datetime | None = None
    sent: bool = False
    alert_action_id: str = ""
    answers: list[dict] = field(default_factory=list)
    round_index: int = 1


class IncidentManager:
    def __init__(self, home: HomeConfig) -> None:
        self.home = home
        self.incident: Incident | None = None
        self._latched = False

    def active(self) -> Incident | None:
        if self.incident and self.incident.phase in {
            "speaking_1",
            "window_1",
            "speaking_2",
            "window_2",
            "alert_pending",
            "alerted",
        }:
            return self.incident
        return self.incident if self.incident and self.incident.phase == "alerted" else None

    def open_possible_fall(self, event: PossibleFallEvent, now: datetime) -> list[SafetyCommand]:
        gap = (event.floor_at - event.descent_at).total_seconds()
        if gap < 2:
            return []
        if event.source not in {"inference", "developer", "fixture"}:
            return []
        return self._open(event.session_id, event.room, event.source, now)

    def open_help_request(self, session_id: str, room: str, now: datetime, source: str) -> list[SafetyCommand]:
        if self.incident and self.incident.phase == "alert_pending":
            return []
        if self.incident and self.incident.phase in {"speaking_1", "window_1", "speaking_2", "window_2"}:
            return self._escalate_now(now, reason="help")
        if self.incident and self.incident.phase not in {"resolved_ok", "cancelled", "idle"}:
            return []
        incident = Incident(
            incident_id=new_id("inc"),
            session_id=session_id,
            room=room,
            phase="alert_pending",
            opened_at=now,
            source=source,
            alert_action_id=new_id("act"),
        )
        self.incident = incident
        self._latched = True
        return [self._stop(), *self._escalate_now(now, reason="help")]

    def _open(self, session_id: str, room: str, source: str, now: datetime) -> list[SafetyCommand]:
        if self._latched and self.incident and self.incident.phase not in {"resolved_ok", "cancelled"}:
            return []
        if self.incident and self.incident.phase not in {"resolved_ok", "cancelled", "idle"}:
            return []
        incident = Incident(
            incident_id=new_id("inc"),
            session_id=session_id,
            room=room,
            phase="speaking_1",
            opened_at=now,
            source=source,
            alert_action_id=new_id("act"),
        )
        self.incident = incident
        self._latched = True
        return [self._stop(), *self._ask(incident, 1, now)]

    def _stop(self) -> SafetyCommand:
        assert self.incident is not None
        return SafetyCommand(kind="stop_sound", incident_id=self.incident.incident_id)

    def _ask(self, incident: Incident, round_index: int, now: datetime) -> list[SafetyCommand]:
        text = self.home.checkin_1 if round_index == 1 else self.home.checkin_2
        question_id = new_id("qst")
        message_id = new_id("msg")
        incident.round_index = round_index
        incident.question_id = question_id
        incident.question_text = text
        incident.message_id = message_id
        incident.phase = "speaking_1" if round_index == 1 else "speaking_2"
        incident.window_deadline = None
        incident.speak_deadline = now + timedelta(seconds=self.home.speech_total_cap_s)
        return [
            SafetyCommand(
                kind="announce",
                incident_id=incident.incident_id,
                text=text,
                message_id=message_id,
                question_id=question_id,
            )
        ]

    def on_playback_ended(self, message_id: str, now: datetime) -> None:
        incident = self.incident
        if incident is None or incident.message_id != message_id:
            return
        if incident.phase == "speaking_1":
            incident.phase = "window_1"
        elif incident.phase == "speaking_2":
            incident.phase = "window_2"
        else:
            return
        incident.window_deadline = now + timedelta(seconds=self.home.safety_window_s)

    def on_audio_unavailable(self, message_id: str, now: datetime) -> None:
        incident = self.incident
        if incident is None or incident.message_id != message_id:
            return
        incident.audio_failed = True
        self.on_playback_ended(message_id, now)

    def poll(self, now: datetime) -> list[SafetyCommand]:
        incident = self.incident
        if incident is None:
            return []
        if incident.phase in {"speaking_1", "speaking_2"} and incident.speak_deadline and now >= incident.speak_deadline:
            self.on_audio_unavailable(incident.message_id or "", now)
        if incident.phase == "window_1" and incident.window_deadline and now >= incident.window_deadline:
            return self._ask(incident, 2, now)
        if incident.phase == "window_2" and incident.window_deadline and now >= incident.window_deadline:
            return self._escalate_now(now, reason="timeout")
        return []

    def answer(self, question_id: str, text: str | None, button: str | None, now: datetime) -> list[SafetyCommand]:
        incident = self.incident
        if incident is None or incident.question_id != question_id:
            return []
        if incident.phase not in {"speaking_1", "window_1", "speaking_2", "window_2"}:
            return []
        label = button or ""
        if label in {"im_okay", "okay"}:
            kind = "okay"
        elif label in {"need_help", "help"}:
            kind = "help"
        elif text:
            kind = classify_reply(text)
        else:
            kind = "ambiguous"
        incident.answers.append({"at": now.isoformat(), "kind": kind, "question_id": question_id})
        if kind == "okay":
            incident.phase = "resolved_ok"
            incident.window_deadline = None
            self._latched = False
            return [SafetyCommand(kind="resolved", incident_id=incident.incident_id, text="Check-in resolved.")]
        if kind == "help":
            return self._escalate_now(now, reason="help")
        return []

    def _escalate_now(self, now: datetime, reason: str) -> list[SafetyCommand]:
        incident = self.incident
        if incident is None or incident.sent or incident.phase in {"alerted", "resolved_ok", "cancelled"}:
            return []
        incident.phase = "alert_pending"
        message = self.home.alert_template
        if incident.audio_failed:
            message = f"{message} {self.home.audio_failure_note}"
        return [
            SafetyCommand(
                kind="alert",
                incident_id=incident.incident_id,
                text=message,
                severity="high",
                action_id=incident.alert_action_id,
                question_id=reason,
            )
        ]

    def claim_send(self, incident_id: str) -> bool:
        incident = self.incident
        if incident is None or incident.incident_id != incident_id:
            return False
        if incident.sent or incident.phase != "alert_pending":
            return False
        incident.sent = True
        incident.phase = "alerted"
        return True

    def mark_submitted(self, already: bool = True) -> str:
        incident = self.incident
        if incident is None:
            return "no incident"
        if incident.phase == "alerted" or incident.sent:
            return "the message has already been submitted"
        if not already:
            incident.phase = "cancelled"
            self._latched = False
        return "cancelled before send"

    def pause_sensing(self) -> str:
        incident = self.incident
        if incident is None:
            return "monitoring is stopped"
        if incident.sent or incident.phase == "alerted":
            return "the message has already been submitted"
        incident.phase = "cancelled"
        incident.window_deadline = None
        self._latched = False
        return "monitoring has stopped"

    def note_provider_result(self, status: str) -> None:
        incident = self.incident
        if incident is None:
            return
        if status in {"accepted", "delivered", "unknown", "failed"}:
            incident.phase = "alerted" if status != "failed" else "alerted"
            incident.sent = status != "failed"


from fluffy.brain.safety_legacy import (  # noqa: E402
    CHECKIN_TEXT,
    PAIN_TEXT_S,
    SafetyEffect,
    SafetyMachine,
)
