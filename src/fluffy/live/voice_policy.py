"""How often Fluffy may speak up on its own.

Safety check-ins and answers to something the person just said are not
limited here. An unprompted question waits if one was just asked, if nobody
is in view, if this offer was already declined, or after two silences.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fluffy.clock import Clock

UNPROMPTED_GAP = timedelta(minutes=3)
QUIET_AFTER_SILENCE = timedelta(minutes=10)
DECLINE_WINDOW = timedelta(minutes=30)


class VoicePolicy:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.last_unprompted: datetime | None = None
        self.unanswered = 0
        self.quiet_until: datetime | None = None
        self.declined: dict[str, datetime] = {}

    def hold_reason(
        self,
        *,
        someone_present: bool,
        question_open: bool,
        question: str,
    ) -> str | None:
        """Why this unprompted question should wait, or None if it may be asked."""
        if question_open:
            return "a question is already open"
        if not someone_present:
            return "nobody in view"
        if self.declined_recently(question):
            return "this offer was declined in the last 30 minutes"
        now = self.clock.now()
        if self.quiet_until is not None and now < self.quiet_until:
            return "holding off after unanswered questions"
        if self.last_unprompted is not None and now - self.last_unprompted < UNPROMPTED_GAP:
            return "asked something in the last 3 minutes"
        return None

    def note_unprompted(self) -> None:
        self.last_unprompted = self.clock.now()

    def note_answered(self) -> None:
        self.unanswered = 0

    def note_unanswered(self) -> None:
        self.unanswered += 1
        if self.unanswered >= 2:
            self.quiet_until = self.clock.now() + QUIET_AFTER_SILENCE
            self.unanswered = 0

    def note_declined_question(self, question: str) -> None:
        text = question.strip().lower()
        if text:
            self.declined[text] = self.clock.now()

    def declined_recently(self, question: str) -> bool:
        at = self.declined.get(question.strip().lower())
        if at is None:
            return False
        return self.clock.now() - at < DECLINE_WINDOW
