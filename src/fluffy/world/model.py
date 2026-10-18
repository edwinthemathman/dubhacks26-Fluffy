"""World model: room context, object memory, and the events that start a turn."""

import hashlib
import re
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from fluffy.clock import Clock
from fluffy.ids import IdFactory
from fluffy.perception.scene import SceneState

HandledAs = Literal["safety", "notify", "offer", "context"]
OBJECT_FRESH = timedelta(minutes=15)
MEMORY_WINDOW = timedelta(minutes=30)
MAX_UTTERANCES = 10
SNAPSHOT_TOKEN_BUDGET = 600

# "help" alone is distress. "help me find my keys" is a normal request.
_DISTRESS = (
    re.compile(r"\bi fell\b", re.IGNORECASE),
    re.compile(r"\bcan(?:not|'t) get up\b", re.IGNORECASE),
    re.compile(r"\bi need help\b", re.IGNORECASE),
    re.compile(r"^\s*(?:fluffy[, ]+)?help(?: me)?[.!?]*\s*$", re.IGNORECASE),
)

_HANDLED: dict[str, HandledAs] = {
    "possible_fall": "safety",
    "distress_call": "safety",
    "doorbell": "notify",
    "knock": "notify",
    "smoke_alarm": "notify",
    "name_called": "notify",
    "pointing_at_device": "context",
    "glare_signal": "offer",
    "getting_dark": "offer",
}

_SOUND_LABELS = {"doorbell", "knock", "smoke_alarm", "name_called"}


class DerivedEvent(BaseModel):
    id: str
    kind: str
    at: datetime
    handled_as: HandledAs
    detail: dict[str, str] = Field(default_factory=dict)


class Recall(BaseModel):
    name: str
    location_desc: str | None = None
    last_seen_at: datetime | None = None
    status: Literal["fresh", "stale", "unknown"]


class ContextSnapshot(BaseModel):
    id: str
    text: str
    approx_tokens: int


class ObjectMemory:
    def __init__(self) -> None:
        self._items: dict[str, tuple[datetime, str, str]] = {}

    def update(self, name: str, location_desc: str, at: datetime) -> None:
        key = _object_key(name)
        self._items[key] = (at, location_desc, name.strip())

    def recall(self, name: str, now: datetime) -> Recall:
        key = _object_key(name)
        found = self._items.get(key)
        if found is None:
            for stored, value in self._items.items():
                if key in stored or stored in key:
                    found = value
                    break
        if found is None:
            return Recall(name=name, status="unknown")
        seen_at, location, display = found
        status: Literal["fresh", "stale"] = (
            "fresh" if now - seen_at <= OBJECT_FRESH else "stale"
        )
        return Recall(
            name=display,
            location_desc=location,
            last_seen_at=seen_at,
            status=status,
        )

    def lines(self, now: datetime) -> list[str]:
        rows = []
        for seen_at, location, display in self._items.values():
            if now - seen_at > MEMORY_WINDOW:
                continue
            age = int((now - seen_at).total_seconds() // 60)
            rows.append(f"{display}: {location} ({age} min ago)")
        return rows


class WorldModel:
    def __init__(self, clock: Clock, ids: IdFactory | None = None) -> None:
        self.clock = clock
        self.ids = ids or IdFactory()
        self.objects = ObjectMemory()
        self.speech_active = False
        self.latest_scene: SceneState | None = None
        self.devices: dict[str, str] = {}
        self.utterances: list[tuple[datetime, str]] = []
        self.plan_notes: list[tuple[datetime, str]] = []
        self.events: list[DerivedEvent] = []
        self._held: dict[str, datetime] = {}
        self._fired: set[str] = set()

    def observe_scene(self, scene: SceneState) -> list[DerivedEvent]:
        now = self.clock.now()
        self.latest_scene = scene
        for item in scene.objects_seen:
            self.objects.update(item.name, item.location_desc, now)

        found: list[DerivedEvent] = []
        found.extend(
            self._sustain(
                "possible_fall",
                _fall_active(scene),
                10,
                {"surface": "floor"},
            )
        )
        found.extend(
            self._sustain(
                "glare_signal",
                any(person.gesture == "shielding_eyes" for person in scene.people),
                10,
                {},
            )
        )
        found.extend(
            self._sustain(
                "getting_dark",
                scene.lighting == "dark",
                10,
                {"lighting": scene.lighting},
            )
        )
        pointer = next(
            (
                person
                for person in scene.people
                if person.gesture == "pointing" and person.attention_target
            ),
            None,
        )
        found.extend(
            self._sustain(
                "pointing_at_device",
                self.speech_active and pointer is not None,
                0,
                {"attention_target": pointer.attention_target or ""} if pointer else {},
            )
        )
        return found

    def observe_transcript(self, text: str) -> list[DerivedEvent]:
        cleaned = text.strip()
        if not cleaned:
            return []
        self.utterances.append((self.clock.now(), cleaned))
        self.utterances = self.utterances[-MAX_UTTERANCES:]
        if any(pattern.search(cleaned) for pattern in _DISTRESS):
            return [self._emit("distress_call", {"utterance": cleaned})]
        return []

    def observe_sound(self, label: str) -> list[DerivedEvent]:
        if label not in _SOUND_LABELS:
            return []
        return [self._emit(label, {})]

    def observe_sos(self) -> list[DerivedEvent]:
        return [self._emit("distress_call", {"source": "sos_button"})]

    def note_plan(self, summary: str) -> None:
        now = self.clock.now()
        self.plan_notes.append((now, summary))
        cutoff = now - MEMORY_WINDOW
        self.plan_notes = [(at, text) for at, text in self.plan_notes if at >= cutoff]

    def mirror_devices(self, states: dict[str, str]) -> None:
        self.devices = dict(states)

    def recall_object(self, name: str) -> Recall:
        return self.objects.recall(name, self.clock.now())

    def snapshot(self, *, profile_summary: str, open_questions: list[str]) -> ContextSnapshot:
        now = self.clock.now()
        cutoff = now - MEMORY_WINDOW
        recent_events = [event for event in self.events if event.at >= cutoff][-5:]
        recent_plans = [text for at, text in self.plan_notes if at >= cutoff][-3:]
        utterances = [text for _, text in self.utterances]

        def render(lines_of_speech: list[str]) -> str:
            scene_line = "none yet"
            attention = ""
            if self.latest_scene is not None:
                scene_line = self.latest_scene.summary or _fallback_summary(self.latest_scene)
                target = next(
                    (
                        person.attention_target
                        for person in self.latest_scene.people
                        if person.attention_target
                    ),
                    None,
                )
                if target:
                    attention = f"attention: {target}"
            event_line = ", ".join(event.kind for event in recent_events) or "none"
            question_line = " | ".join(open_questions) or "none"
            object_line = "; ".join(self.objects.lines(now)) or "none"
            device_line = ", ".join(f"{name}={value}" for name, value in self.devices.items()) or "defaults"
            plan_line = " | ".join(recent_plans) or "none"
            speech_line = " | ".join(lines_of_speech) or "none"
            parts = [
                f"profile: {profile_summary}",
                f"room: {scene_line}",
                attention,
                f"devices: {device_line}",
                f"events: {event_line}",
                f"said: {speech_line}",
                f"questions: {question_line}",
                f"objects: {object_line}",
                f"plans: {plan_line}",
            ]
            return "\n".join(part for part in parts if part)

        text = render(utterances)
        while _tokens(text) > SNAPSHOT_TOKEN_BUDGET and len(utterances) > 1:
            utterances = utterances[1:]
            text = render(utterances)
        digest = hashlib.sha256(text.encode()).hexdigest()[:12]
        return ContextSnapshot(id=f"ctx_{digest}", text=text, approx_tokens=_tokens(text))

    def _sustain(
        self,
        kind: str,
        active: bool,
        dwell_s: float,
        detail: dict[str, str],
    ) -> list[DerivedEvent]:
        if not active:
            self._held.pop(kind, None)
            self._fired.discard(kind)
            return []
        if kind in self._fired:
            return []
        started = self._held.setdefault(kind, self.clock.now())
        if (self.clock.now() - started).total_seconds() < dwell_s:
            return []
        self._fired.add(kind)
        return [self._emit(kind, detail)]

    def _emit(self, kind: str, detail: dict[str, str]) -> DerivedEvent:
        event = DerivedEvent(
            id=self.ids("evt"),
            kind=kind,
            at=self.clock.now(),
            handled_as=_HANDLED[kind],
            detail=detail,
        )
        self.events.append(event)
        return event


def _fall_active(scene: SceneState) -> bool:
    return any(
        person.on_floor and person.location_surface not in {"sofa", "bed"}
        for person in scene.people
    )


def _fallback_summary(scene: SceneState) -> str:
    count = len(scene.people)
    noun = "person" if count == 1 else "people"
    return f"{count} {noun}, lighting {scene.lighting}"


def _object_key(name: str) -> str:
    return " ".join(name.strip().lower().split())


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)
