"""Whether the room just became occupied or empty.

The first read only sets a baseline, so opening the camera is not an arrival.
Walking in is one read after the room was empty. Walking out waits until the
person has been gone, and a fall is never treated as leaving.
"""

from __future__ import annotations

from dataclasses import dataclass

from fluffy.brain.cues import derive
from fluffy.brain.habits import room_change_line
from fluffy.perception.scene import SceneState


@dataclass(frozen=True)
class PresenceRead:
    cues: set[str]
    room_change: str
    empty_for_s: float = 0


class RoomPresence:
    def __init__(self) -> None:
        self.ready = False
        self.occupied = False
        self.confirmed_empty = False
        self.empty_streak = 0
        self.empty_first_at: float | None = None
        self.saw_hazard = False
        self.last_upright = False
        self.last_down = False

    def observe(self, scene: SceneState, now: float, name: str) -> PresenceRead:
        someone = bool(scene.people)
        if not self.ready:
            self.ready = True
            self.occupied = someone
            if someone:
                self._remember(scene)
            else:
                self.empty_streak = 1
                self.empty_first_at = now
            return PresenceRead(set(), "")

        if someone:
            entered = self.confirmed_empty and self.empty_streak >= 2
            empty_for = 0.0 if self.empty_first_at is None else max(0.0, now - self.empty_first_at)
            self.occupied = True
            self.confirmed_empty = False
            self.empty_streak = 0
            self.empty_first_at = None
            self.saw_hazard = False
            self._remember(scene)
            if not entered:
                return PresenceRead(set(), "")
            return PresenceRead(
                {"entered_room"},
                room_change_line(name, {"entered_room"}, empty_for_s=empty_for),
                empty_for,
            )

        if self.empty_streak == 0:
            self.empty_first_at = now
            self.saw_hazard = _hazard(scene)
        else:
            self.saw_hazard = self.saw_hazard or _hazard(scene)
        self.empty_streak += 1
        span = 0.0 if self.empty_first_at is None else now - self.empty_first_at
        if (
            self.occupied
            and self.empty_streak >= 2
            and span >= 5
            and not self.saw_hazard
            and self.last_upright
            and not self.last_down
        ):
            self.occupied = False
            self.confirmed_empty = True
            self.last_upright = False
            return PresenceRead({"left_room"}, room_change_line(name, {"left_room"}))
        if not self.occupied and self.empty_streak >= 2 and not self.saw_hazard:
            self.confirmed_empty = True
        return PresenceRead(set(), "")

    def _remember(self, scene: SceneState) -> None:
        upright = False
        down = False
        for person in scene.people:
            if person.posture == "lying" or person.on_floor or person.fell or person.activity == "fallen":
                down = True
            if person.posture in {"standing", "walking"} or person.activity == "leaving":
                upright = True
        self.last_down = down
        self.last_upright = upright and not down


def _hazard(scene: SceneState) -> bool:
    if "fall" in derive(scene):
        return True
    return bool(set(scene.sounds) & {"impact", "pain"})
