"""World model: room context, object memory, and the events that start a turn."""

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from fluffy.clock import Clock
from fluffy.ids import IdFactory
from fluffy.perception.scene import ObjectSighting, SceneState

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
    home_desc: str | None = None
    unconfirmed: bool = False
    with_person: bool = False
    on_floor: bool = False
    near_person: bool = False
    side: str = "unclear"
    on_head: bool = False


@dataclass
class Tag:
    name: str
    location_desc: str
    last_seen_at: datetime
    with_person: bool = False
    on_floor: bool = False
    near_person: bool = False
    home_desc: str | None = None
    unconfirmed: bool = False
    side: str = "unclear"
    on_head: bool = False
    misses: int = 0
    sightings: int = 1


class ContextSnapshot(BaseModel):
    id: str
    text: str
    approx_tokens: int


class ObjectMemory:
    def __init__(self) -> None:
        self._items: dict[str, Tag] = {}

    def update(self, name: str, location_desc: str, at: datetime) -> None:
        self.note_live([ObjectSighting(name=name, location_desc=location_desc)], at)

    def note_scan(self, sightings: list[ObjectSighting], at: datetime) -> None:
        """A room scan sets the usual spot. The first time, that is also last seen."""
        seen: set[str] = set()
        for item in sightings:
            key = _object_key(item.name)
            if not key:
                continue
            seen.add(key)
            place = item.location_desc.strip() or "in the room"
            tag = self._items.get(key)
            if tag is None:
                self._items[key] = Tag(
                    name=item.name.strip(),
                    location_desc=place,
                    last_seen_at=at,
                    with_person=item.with_person,
                    on_floor=item.on_floor,
                    near_person=item.near_person,
                    home_desc=place,
                    side=item.side,
                    on_head=item.on_head,
                )
            else:
                # A scan is a look at where things are now, so a move replaces the old spot.
                tag.home_desc = place
                tag.location_desc = place
                tag.with_person = item.with_person
                tag.on_floor = item.on_floor
                tag.near_person = item.near_person
                tag.side = item.side
                tag.on_head = item.on_head
                tag.last_seen_at = at
                tag.name = item.name.strip() or tag.name
                tag.unconfirmed = False
                tag.misses = 0
        for key in [key for key in self._items if key not in seen]:
            del self._items[key]

    def note_live(self, sightings: list[ObjectSighting], at: datetime) -> None:
        """Update last seen. An object missing from this read stays, marked unconfirmed."""
        seen: set[str] = set()
        for item in sightings:
            key = _object_key(item.name)
            if not key:
                continue
            seen.add(key)
            place, side = _split_side(item.location_desc.strip() or "in the room", item.side)
            held = item.with_person or _held_place(place)
            tag = self._items.get(key)
            if tag is None:
                self._items[key] = Tag(
                    name=item.name.strip(),
                    location_desc=place,
                    last_seen_at=at,
                    with_person=held,
                    on_floor=item.on_floor,
                    near_person=item.near_person,
                    side=side,
                    on_head=item.on_head,
                )
                continue
            tag.location_desc = place
            tag.with_person = held
            tag.on_floor = item.on_floor
            tag.near_person = item.near_person
            tag.side = side
            tag.on_head = item.on_head
            tag.last_seen_at = at
            tag.unconfirmed = False
            tag.misses = 0
            tag.sightings += 1
            tag.name = item.name.strip() or tag.name
        gone = []
        for key, tag in self._items.items():
            if key in seen:
                continue
            tag.misses += 1
            if tag.misses >= 2:
                tag.unconfirmed = True
            if tag.misses >= 5:
                gone.append(key)
        for key in gone:
            if self._items[key].misses >= 5:
                del self._items[key]

    def floor_flags(self) -> dict[str, bool]:
        return {key: tag.on_floor for key, tag in self._items.items()}

    def tags(self, now: datetime, important: set[str] | None = None) -> list[Tag]:
        wanted = important or set()
        rows = [
            tag
            for tag in self._items.values()
            if now - tag.last_seen_at <= OBJECT_FRESH or tag.home_desc
        ]
        rows.sort(key=lambda tag: (0 if _object_key(tag.name) in wanted else 1, tag.name.lower()))
        return rows

    def recall(self, name: str, now: datetime) -> Recall:
        found = self._find(name)
        if found is None:
            return Recall(name=name, status="unknown")
        status: Literal["fresh", "stale"] = (
            "fresh" if now - found.last_seen_at <= OBJECT_FRESH else "stale"
        )
        return Recall(
            name=found.name,
            location_desc=found.location_desc,
            last_seen_at=found.last_seen_at,
            status=status,
            home_desc=found.home_desc,
            unconfirmed=found.unconfirmed,
            with_person=found.with_person,
            on_floor=found.on_floor,
            near_person=found.near_person,
            side=found.side,
            on_head=found.on_head,
        )

    def lines(self, now: datetime) -> list[str]:
        rows = []
        for tag in self._items.values():
            if now - tag.last_seen_at > MEMORY_WINDOW and not tag.home_desc:
                continue
            age = int((now - tag.last_seen_at).total_seconds() // 60)
            rows.append(f"{tag.name}: {tag.location_desc} ({age} min ago)")
        return rows

    def _find(self, name: str) -> Tag | None:
        key = _object_key(name)
        if not key:
            return None
        return self._items.get(key)


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
        scene = scene.model_copy(update={"objects_seen": objects_at_end(scene)})
        self.latest_scene = scene
        self.objects.note_live(scene.objects_seen, now)

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


_ALIASES = {
    "key": "keys",
    "cell phone": "phone",
    "cellphone": "phone",
    "mobile": "phone",
    "mobile phone": "phone",
    "reading glasses": "glasses",
    "spectacles": "glasses",
    "water bottle": "water",
    "bottle of water": "water",
    "drinking glass": "glass",
    "mug": "cup",
}


def _object_key(name: str) -> str:
    text = " ".join(name.strip().lower().split())
    return _ALIASES.get(text, text)


_OBJECT_NAMES = (
    "hearing aids",
    "pill organizer",
    "pill box",
    "phone",
    "keys",
    "wallet",
    "glasses",
    "cane",
    "walker",
    "remote",
    "water bottle",
    "water",
    "glass",
    "mug",
    "cup",
    "bag",
    "book",
    "charger",
    "pills",
)
_HELD_WORDS = ("hand", "hands", "holding", "held", "pocket", "pockets")


def objects_at_end(scene: SceneState) -> list[ObjectSighting]:
    """One tag per object, where it is after the clip, not where it started.

    A phone in the hand and then on the table stays on the table.
    """
    chosen: dict[str, ObjectSighting] = {}
    for item in scene.objects_seen:
        key = _object_key(item.name)
        if not key:
            continue
        chosen[key] = _end_of_sighting(item)
    for key, moved in _moves(scene).items():
        current = chosen.get(key)
        if not moved.with_person and (current is None or current.with_person or _held_place(current.location_desc)):
            name = current.name if current is not None else moved.name
            chosen[key] = moved.model_copy(update={"name": name})
        elif moved.with_person and (current is None or not current.with_person):
            name = current.name if current is not None else moved.name
            chosen[key] = moved.model_copy(update={"name": name})
    return list(chosen.values())


def _end_of_sighting(item: ObjectSighting) -> ObjectSighting:
    parts = re.split(r"\b(?:then|after that|and then)\b", item.location_desc, flags=re.IGNORECASE)
    last = parts[-1].strip(" .")
    if len(parts) < 2 or not last:
        return item
    held = _held_place(last)
    return item.model_copy(
        update={
            "location_desc": last,
            "with_person": held,
            "on_floor": "floor" in last.lower() or "ground" in last.lower(),
        }
    )


def _moves(scene: SceneState) -> dict[str, ObjectSighting]:
    names = list(_OBJECT_NAMES)
    for item in scene.objects_seen:
        key = _object_key(item.name)
        if key and key not in names:
            names.append(key)
    names.sort(key=len, reverse=True)
    if not names:
        return {}
    name_re = "|".join(re.escape(name) for name in names)
    set_down = re.compile(
        rf"\b(?:puts?|sets?|places?|placed|lays?|laid|leaves|left|drops?)\s+"
        rf"(?:down\s+)?(?:the\s+|their\s+|a\s+|his\s+|her\s+)?({name_re})\s+"
        rf"(?:down\s+)?(?:on|onto)\s+(?:the\s+|a\s+)?([a-z0-9][^.,;]{{0,40}})",
        re.IGNORECASE,
    )
    pick_up = re.compile(
        rf"\b(?:picks?\s+up|picked\s+up|grabs?|grabbed)\s+"
        rf"(?:the\s+|their\s+|a\s+|his\s+|her\s+)?({name_re})\b",
        re.IGNORECASE,
    )
    it_down = re.compile(
        r"\b(?:puts?|sets?|places?|placed|lays?|laid|leaves|left|drops?)\s+"
        r"(?:down\s+)?(?:it|them)\s+(?:down\s+)?(?:on|onto)\s+(?:the\s+|a\s+)?"
        r"([a-z0-9][^.,;]{0,40})",
        re.IGNORECASE,
    )
    pronoun = _pronoun_target(scene)
    found: dict[str, ObjectSighting] = {}
    for line in [*scene.timeline, scene.summary]:
        hits: list[tuple[int, ObjectSighting]] = []
        for match in set_down.finditer(line):
            place = re.split(r"\b(?:and|then|while|before|after)\b", match.group(2), maxsplit=1)[0]
            place = " ".join(place.split())
            if not place:
                continue
            where = f"on the {place}"
            hits.append(
                (
                    match.start(),
                    ObjectSighting(
                        name=match.group(1),
                        location_desc=where,
                        with_person=False,
                        on_floor=place.lower().startswith(("floor", "ground")),
                    ),
                )
            )
        for match in pick_up.finditer(line):
            hits.append(
                (
                    match.start(),
                    ObjectSighting(
                        name=match.group(1),
                        location_desc="in their hand",
                        with_person=True,
                        near_person=True,
                    ),
                )
            )
        if pronoun:
            for match in it_down.finditer(line):
                place = re.split(r"\b(?:and|then|while|before|after)\b", match.group(1), maxsplit=1)[0]
                place = " ".join(place.split())
                if not place:
                    continue
                hits.append(
                    (
                        match.start(),
                        ObjectSighting(
                            name=pronoun,
                            location_desc=f"on the {place}",
                            with_person=False,
                            on_floor=place.lower().startswith(("floor", "ground")),
                        ),
                    )
                )
        for _, sighting in sorted(hits, key=lambda item: item[0]):
            found[_object_key(sighting.name)] = sighting
    return found


def _pronoun_target(scene: SceneState) -> str | None:
    """'puts it on the table' means the one thing they were holding, or the only object."""
    rows = [( _object_key(item.name), item) for item in scene.objects_seen]
    rows = [(key, item) for key, item in rows if key]
    if len(rows) == 1:
        return rows[0][0]
    held = [key for key, item in rows if item.with_person or _held_place(item.location_desc)]
    if len(held) == 1:
        return held[0]
    return None


def _split_side(place: str, side: str) -> tuple[str, str]:
    """Left and right belong in side, not in the place name."""
    found = "unclear"
    if re.search(r"\bleft\b", place, re.IGNORECASE):
        found = "left"
    elif re.search(r"\bright\b", place, re.IGNORECASE):
        found = "right"
    cleaned = re.sub(r"\b(left|right)\b", "", place, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,")
    return cleaned or place, side if side not in {"", "unclear"} else found


def _held_place(text: str) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in _HELD_WORDS)


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)
