"""Standing habits the person asked for ahead of time.

A habit is consent they already gave: when its cue is happening, Fluffy may
act without asking. The book is the seam a later "learn this" question can
add to. Learning is not built yet.
"""

from __future__ import annotations

import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from fluffy.clock import Clock
from fluffy.ids import IdFactory
from fluffy.models import Action, Consent, Plan, Trigger, build_plan
from fluffy.models.tool_catalog import action_class, validate_args
from fluffy.perception.scene import SceneState
from fluffy.profile.model import Profile

_WHEN_THEY = {
    "entered_room": "when they walk in",
    "left_room": "when they leave",
    "falling_asleep": "when they doze off",
}
_WHEN_I = {
    "entered_room": "when I walk in",
    "left_room": "when I leave",
    "falling_asleep": "when I doze off",
}


class HabitAction(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class Habit(BaseModel):
    id: str
    said: str
    when: str
    only_if: dict[str, list[str]] = Field(default_factory=dict)
    actions: list[HabitAction]
    min_confidence: float = 0.5
    cooldown_s: float = 120


class HabitBook:
    """The habits Fluffy knows, and how recently each one acted."""

    def __init__(self, habits: list[Habit] | None = None) -> None:
        self.habits = list(habits or [])
        self.used_at: dict[str, datetime] = {}

    def add(self, habit: Habit) -> None:
        _check_actions(habit)
        self.habits.append(habit)

    def matching(self, cues: set[str], scene: SceneState | None, now: datetime) -> list[Habit]:
        """Habits whose cue is happening, whose conditions hold, and which are not cooling down."""
        return [
            habit
            for habit in self.habits
            if habit.when in cues and _conditions_hold(habit, scene) and not self._cooling(habit, now)
        ]

    def covers(self, habit: Habit, plan: Plan) -> bool:
        """The plan is the habit's action, with lights heading the same way."""
        if sorted(action.tool for action in habit.actions) != sorted(action.tool for action in plan.actions):
            return False
        used = [False] * len(plan.actions)
        for wanted in habit.actions:
            found = None
            for index, candidate in enumerate(plan.actions):
                if used[index] or candidate.tool != wanted.tool:
                    continue
                if _args_agree(wanted, candidate):
                    found = index
                    break
            if found is None:
                return False
            used[found] = True
        return True

    def covering(self, plan: Plan, cues: set[str], scene: SceneState | None, now: datetime) -> Habit | None:
        for habit in self.matching(cues, scene, now):
            if self.covers(habit, plan):
                return habit
        return None

    def plan_for(
        self,
        habit: Habit,
        *,
        profile: Profile,
        clock: Clock,
        ids: IdFactory,
        snapshot_id: str = "habit",
    ) -> Plan:
        actions = [
            Action(action_id=ids("act"), tool=action.tool, args=dict(action.args))
            for action in habit.actions
        ]
        classes = [action_class(action.tool) for action in actions]
        chosen = "comfort" if "comfort" in classes else "safety" if "safety" in classes else "notify"
        return build_plan(
            clock=clock,
            ids=ids,
            trigger=Trigger(kind="inferred", room="living_room"),
            action_class=chosen,  # type: ignore[arg-type]
            intent=habit.said.rstrip("."),
            actions=actions,
            confidence=habit.min_confidence,
            consent=Consent(required=chosen == "comfort"),
            needs=list(profile.needs),
            delivered_via=profile.delivery_channels(),
            reasoning_summary=habit_why(habit),
            snapshot_id=snapshot_id,
        )

    def prompt_lines(self, habits: list[Habit], profile: Profile) -> list[str]:
        name = profile.agreements.person_name
        return [f"{name}'s habit: {_prompt_clause(habit)}." for habit in habits]

    def note_used(self, habit: Habit, now: datetime) -> None:
        self.used_at[habit.id] = now

    def _cooling(self, habit: Habit, now: datetime) -> bool:
        used = self.used_at.get(habit.id)
        if used is None:
            return False
        return (now - used).total_seconds() < habit.cooldown_s


def load_habits(path: Path) -> HabitBook:
    """Read the config. A bad tool argument fails here, not mid-demo."""
    data = tomllib.loads(path.read_text())
    book = HabitBook()
    for raw in data.get("habit", []):
        book.add(Habit.model_validate(raw))
    return book


def habit_short(habit: Habit) -> str:
    """'lights on when I walk in'."""
    return f"{_bare_actions(habit)} {_WHEN_I.get(habit.when, habit.when.replace('_', ' '))}"


def habit_why(habit: Habit) -> str:
    return f"habit: {habit_short(habit)}"


def habit_note(habit: Habit) -> str:
    return f"habit applies: {habit_short(habit)} → acting at {habit.min_confidence:g}"


def clip_transition_cues(scene: SceneState | None) -> set[str]:
    """A clip has no history, so arriving and leaving stand in for the room tracker."""
    if scene is None:
        return set()
    cues: set[str] = set()
    for person in scene.people:
        if person.activity == "arriving":
            cues.add("entered_room")
        elif person.activity == "leaving":
            cues.add("left_room")
    return cues


def room_change_line(name: str, cues: set[str], *, empty_for_s: float | None = None) -> str:
    if "entered_room" in cues:
        if empty_for_s is None:
            return f"{name} just walked in"
        return f"{name} just walked in; the room was empty for {int(empty_for_s)} s"
    if "left_room" in cues:
        return f"{name} just left the room"
    return ""


def _check_actions(habit: Habit) -> None:
    for action in habit.actions:
        validate_args(action.tool, action.args)


def _conditions_hold(habit: Habit, scene: SceneState | None) -> bool:
    if not habit.only_if:
        return True
    if scene is None:
        return False
    for key, allowed in habit.only_if.items():
        if getattr(scene, key, None) not in allowed:
            return False
    return True


def _args_agree(wanted: HabitAction, candidate: Action) -> bool:
    if wanted.tool == "dim_lights":
        return _lights_agree(wanted.args, candidate.args)
    if wanted.tool == "set_light_warmth":
        return wanted.args.get("warmth") == candidate.args.get("warmth")
    return wanted.args == candidate.args


def _lights_agree(wanted: dict[str, Any], got: dict[str, Any]) -> bool:
    habit_level = wanted.get("luminosity")
    plan_level = got.get("luminosity")
    if not isinstance(habit_level, (int, float)) or not isinstance(plan_level, (int, float)):
        return False
    if abs(habit_level - plan_level) <= 20:
        return True
    habit_on, plan_on = habit_level >= 60, plan_level >= 60
    habit_dim, plan_dim = habit_level <= 40, plan_level <= 40
    return (habit_on and plan_on) or (habit_dim and plan_dim)


def _prompt_clause(habit: Habit) -> str:
    when = _WHEN_THEY.get(habit.when, f"when {habit.when.replace('_', ' ')}")
    parts = []
    for action in habit.actions:
        if action.tool == "dim_lights":
            level = int(action.args.get("luminosity", 0))
            if level >= 60:
                parts.append(f"lights on ({level}%)")
            elif level == 0:
                parts.append("lights off")
            else:
                parts.append(f"lights at {level}%")
        elif action.tool == "set_light_warmth":
            parts.append(f"lights {action.args.get('warmth', 'warm')}")
        else:
            parts.append(action.tool.replace("_", " "))
    if len(parts) == 2:
        action_text = f"{parts[0]} and {parts[1]}"
    elif len(parts) > 2:
        action_text = f"{', '.join(parts[:-1])}, and {parts[-1]}"
    else:
        action_text = parts[0] if parts else habit.said.rstrip(".")
    return f"{when}, {action_text}"


def _bare_actions(habit: Habit) -> str:
    parts = []
    for action in habit.actions:
        if action.tool == "dim_lights":
            level = int(action.args.get("luminosity", 0))
            if level >= 60:
                parts.append("lights on")
            elif level == 0:
                parts.append("lights off")
            else:
                parts.append("lights dim")
        elif action.tool == "set_light_warmth":
            parts.append(str(action.args.get("warmth", "warm")))
        else:
            parts.append(action.tool.replace("_", " "))
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    if len(parts) > 2:
        return f"{', '.join(parts[:-1])}, and {parts[-1]}"
    return parts[0] if parts else "that"
