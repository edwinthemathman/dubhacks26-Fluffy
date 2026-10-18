"""Evidence before a help check-in.

A check-in starts from a strong signal, or from supporting signals that
agree. One weak hint is written down and does not ask.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Tier = Literal["strong", "supporting", "weak"]
Sense = Literal["vision", "sound", "words", "model", "button"]
Outcome = Literal["checkin", "confirm", "none"]

WINDOW_S = 20.0
FLOOR_SPAN_S = 6.0


@dataclass(frozen=True)
class HelpSignal:
    name: str
    tier: Tier
    sense: Sense
    at: float


@dataclass
class HelpLadder:
    """Rolling 20 seconds of help evidence."""

    signals: list[HelpSignal] = field(default_factory=list)
    floor_at: list[float] = field(default_factory=list)
    upright: int = 0
    strength: str = "none"
    pending_confirm: bool = False

    def observe(self, signal: HelpSignal) -> Outcome:
        self._trim(signal.at)
        if signal.tier == "weak":
            self.signals.append(signal)
            return "none"
        self.signals.append(signal)
        if signal.tier == "strong" and signal.sense in {"words", "button"}:
            self.strength = "strong"
            self.pending_confirm = False
            return "checkin"
        if signal.tier == "strong":
            self.strength = "strong"
            self.pending_confirm = True
            return "confirm"
        if self._supporting_agrees(signal.at):
            self.strength = "supporting"
            self.pending_confirm = False
            return "checkin"
        return "none"

    def note_floor(self, at: float) -> Outcome:
        self.floor_at = [stamp for stamp in self.floor_at if at - stamp <= WINDOW_S]
        self.floor_at.append(at)
        span = self.floor_at[-1] - self.floor_at[0]
        if len(self.floor_at) >= 2 and span >= FLOOR_SPAN_S:
            return self.observe(HelpSignal("on_floor", "strong", "vision", at))
        return self.observe(HelpSignal("on_floor", "supporting", "vision", at))

    def confirm(self, *, on_floor: str, hurt: str, okay: str) -> Outcome:
        """Only a yes still counts. Unclear is a no."""
        if not self.pending_confirm:
            return "none"
        self.pending_confirm = False
        if okay == "yes":
            self.strength = "none"
            return "none"
        if on_floor == "yes" or hurt == "yes":
            self.strength = "strong"
            return "checkin"
        return "none"

    def note_upright(self) -> bool:
        """True once two reads show the person up and moving."""
        self.upright += 1
        if self.upright >= 2:
            self.upright = 0
            self.strength = "none"
            return True
        return False

    def saw_down(self) -> None:
        self.upright = 0

    def _trim(self, at: float) -> None:
        self.signals = [item for item in self.signals if at - item.at <= WINDOW_S]

    def _supporting_agrees(self, at: float) -> bool:
        recent = [item for item in self.signals if item.tier == "supporting" and at - item.at <= WINDOW_S]
        if len(recent) >= 3:
            return True
        senses = {item.sense for item in recent}
        return len(recent) >= 2 and len(senses) >= 2
