"""Turn plain gestures into one guess about what would help.

Perceptron only names what it can see (`PersonState.actions`). This module
keeps about 20 seconds of those labels and matches them to a need. One
scratch of the nose is not a search. A guess is a complete sentence, ready
for speech. The Mind screen gets a short chain; that chain is never spoken.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

from fluffy.perception.scene import ObjectSighting, PersonState, SceneState
from fluffy.profile.model import Profile
from fluffy.world.model import ObjectMemory, Recall

HISTORY = timedelta(seconds=20)
MUTE_FOR = timedelta(minutes=10)
GESTURE_GAP = timedelta(seconds=10)
FRESH_NOW = timedelta(seconds=45)
FRESH_MINUTES = timedelta(minutes=10)
FACE_LABELS = {"touch_face_eyes", "touch_head_top", "touch_ear"}
DRINKS = ("water", "water bottle", "glass", "mug", "cup")
LEAVE_WITH = ("keys", "wallet", "phone")

_ROOT = Path(__file__).resolve().parents[3]
_WEIGHTS = {
    "context": 0.35,
    "not_with_them": 0.20,
    "in_view": 0.20,
    "usual_spot": 0.10,
    "moved": 0.15,
    "said": 0.50,
    "said_no": -0.50,
}


def load_weights(path: Path | None = None) -> dict[str, float]:
    weights = dict(_WEIGHTS)
    file = path or (_ROOT / "config" / "instincts.toml")
    if not file.is_file():
        return weights
    with file.open("rb") as handle:
        data = tomllib.load(handle)
    found = data.get("weights")
    if isinstance(found, dict):
        for key, value in found.items():
            if key in weights:
                weights[key] = float(value)
    return weights


def load_anchors(path: Path | None = None) -> tuple[str, str]:
    file = path or (_ROOT / "config" / "home.toml")
    left, right = "the window", "the door"
    if not file.is_file():
        return left, right
    with file.open("rb") as handle:
        data = tomllib.load(handle)
    camera = data.get("camera")
    if isinstance(camera, dict):
        left = str(camera.get("left_anchor") or left)
        right = str(camera.get("right_anchor") or right)
    return left, right


@dataclass(frozen=True)
class Guess:
    kind: str
    need: str
    text: str
    choices: tuple[str, ...] = ()
    chain: str = ""
    check: bool = True


@dataclass
class _Hit:
    at: datetime
    label: str
    count: int


class Instincts:
    def __init__(self, anchors: tuple[str, str] | None = None, weights: dict[str, float] | None = None) -> None:
        self.anchors = anchors or load_anchors()
        self.weights = weights or load_weights()
        self.history: list[_Hit] = []
        self.muted: dict[str, datetime] = {}
        self.done: dict[str, datetime] = {}

    def note(self, scene: SceneState, now: datetime) -> None:
        """Remember this read's actions. People on a screen are ignored."""
        self.history = [hit for hit in self.history if now - hit.at <= HISTORY]
        for person in _real_people(scene):
            if person.face_visible == "no":
                actions = [item for item in person.actions if item.label not in FACE_LABELS]
            else:
                actions = list(person.actions)
            for action in actions[:4]:
                self.history.append(_Hit(now, action.label, action.count))

    def mute(self, need: str, now: datetime) -> None:
        self.muted[need] = now + MUTE_FOR

    def note_done(self, need: str, now: datetime) -> None:
        self.done[need] = now

    def guess(
        self,
        scene: SceneState,
        memory: ObjectMemory,
        profile: Profile,
        now: datetime,
        *,
        music_on: bool,
        spoke_recently: bool,
        last_line: str,
    ) -> Guess | None:
        del profile
        people = _real_people(scene)
        person = people[0] if people else None
        counts = self._counts(now)
        sounds = {label for label in scene.sounds if label != "none"}
        on_phone = person is not None and person.on_phone == "yes"
        paused = len(people) >= 2 or on_phone or (person is not None and person.speaking == "yes")

        if counts.get("clutch_throat", 0) >= 1:
            return _safety("choking", counts, "possible choking")
        if counts.get("hold_chest", 0) >= 1:
            return _safety("chest", counts, "hand on chest")

        gesture = person.gesture if person is not None else "none"
        if (
            gesture == "stop"
            and music_on
            and not on_phone
            and not self._cooling("stop_music", now)
            and not self._quiet("stop_music", now)
        ):
            return _gesture("stop_music", "stop_music", "Okay, I stopped the music.", counts, "raised palm")
        cupped = self._frames("cup_ear", now) >= 2 or counts.get("cup_ear", 0) >= 2
        if cupped and not on_phone and not self._quiet("volume_up", now):
            if music_on and not self._cooling("volume_up", now):
                return _gesture("volume_up", "volume_up", "Turning the music up.", counts, "cupped ear")
            if not music_on and spoke_recently and last_line.strip() and not self._cooling("repeat", now):
                return _gesture("repeat", "repeat", speakable(last_line), counts, "could not hear")

        if paused or person is None:
            return None
        found = self._water(person, counts, sounds, scene, memory, now)
        if found is not None:
            return found
        if person.wearing_glasses != "yes":
            found = self._glasses(person, counts, scene, memory, now)
            if found is not None:
                return found
        for finder in (
            self._keys,
            self._hearing,
            self._cane,
            self._dropped,
            self._remote,
            self._lost,
        ):
            found = finder(person, counts, scene, memory, now)
            if found is not None:
                return found
        return None

    def _counts(self, now: datetime) -> dict[str, int]:
        totals: dict[str, int] = {}
        for hit in self.history:
            if now - hit.at <= HISTORY:
                totals[hit.label] = totals.get(hit.label, 0) + hit.count
        return totals

    def _frames(self, label: str, now: datetime) -> int:
        return sum(1 for hit in self.history if hit.label == label and now - hit.at <= HISTORY)

    def _quiet(self, need: str, now: datetime) -> bool:
        until = self.muted.get(need)
        return until is not None and now < until

    def _cooling(self, need: str, now: datetime) -> bool:
        at = self.done.get(need)
        return at is not None and now - at < GESTURE_GAP

    def _glasses(self, person, counts, scene, memory, now) -> Guess | None:
        if self._quiet("glasses", now):
            return None
        face = counts.get("touch_face_eyes", 0)
        companions = (
            counts.get("pat_chest_pocket", 0)
            or counts.get("touch_head_top", 0)
            or counts.get("look_at_surfaces", 0)
        )
        squint = counts.get("hold_text_far_or_squint", 0) >= 2
        on_head_pat = counts.get("touch_head_top", 0) and counts.get("look_at_surfaces", 0)
        if not ((face and companions) or squint or on_head_pat):
            return None
        if person.wearing_glasses != "no":
            return None
        spot = _spot(scene, memory, now, "glasses")
        if spot is not None and spot.on_head:
            text = "I think your glasses might be on top of your head."
            return _object("tell", "glasses", text, counts, person)
        if on_head_pat and spot is None:
            text = "I think your glasses might be on top of your head."
            return _object("tell", "glasses", text, counts, person)
        return self._located("glasses", spot, counts, person, memory, now, scene)

    def _water(self, person, counts, sounds, scene, memory, now) -> Guess | None:
        if self._quiet("water", now):
            return None
        coughs = counts.get("cough", 0) + (1 if "cough" in sounds else 0)
        throat = counts.get("touch_throat", 0)
        if not ((throat and coughs) or coughs >= 2):
            return None
        if _holding_drink(person):
            return None
        spot = _drink_spot(scene, memory, now)
        if spot is None or spot.with_person:
            text = "Would you like some water?"
            return Guess(
                "ask",
                "water",
                speakable(text),
                ("Yes", "No", "I'm fine"),
                _chain(counts, "water", text),
            )
        return self._located(spot.name, spot, counts, person, memory, now, scene, need="water")

    def _keys(self, person, counts, scene, memory, now) -> Guess | None:
        if person.zone != "door":
            return None
        if not counts.get("pat_pants_pockets", 0):
            return None
        if not (counts.get("pat_jacket_or_bag", 0) or counts.get("look_at_surfaces", 0)):
            return None
        if self._quiet("keys", now):
            return None
        spots = [spot for name in LEAVE_WITH if (spot := _spot(scene, memory, now, name)) and not spot.with_person]
        if len(spots) == 1:
            return self._located(spots[0].name, spots[0], counts, person, memory, now, scene, need="keys")
        names = [spot.name for spot in spots] or list(LEAVE_WITH)
        text = _choice_question(names[:3])
        return Guess("ask", "keys", speakable(text), _choice_labels(names[:3]), _chain(counts, "keys", text))

    def _hearing(self, person, counts, scene, memory, now) -> Guess | None:
        if self._quiet("hearing aids", now):
            return None
        if not counts.get("touch_ear", 0):
            return None
        if not (counts.get("look_at_surfaces", 0) or counts.get("look_down_beside_seat", 0)):
            return None
        spot = _spot(scene, memory, now, "hearing aids")
        return self._located("hearing aids", spot, counts, person, memory, now, scene, need="hearing aids")

    def _cane(self, person, counts, scene, memory, now) -> Guess | None:
        if self._quiet("cane", now) or not counts.get("reach_steady", 0):
            return None
        for name in ("cane", "walker"):
            spot = _spot(scene, memory, now, name)
            if spot is None or spot.with_person or spot.near:
                continue
            return self._located(name, spot, counts, person, memory, now, scene, need="cane")
        return None

    def _dropped(self, person, counts, scene, memory, now) -> Guess | None:
        if self._quiet("dropped", now):
            return None
        if not counts.get("look_down_beside_seat", 0):
            return None
        if not (counts.get("lift_or_move_things", 0) or counts.get("stop_and_look_around", 0)):
            return None
        for item in scene.objects_seen:
            if not item.on_floor:
                continue
            spot = _from_sight(item, now)
            return self._located(item.name, spot, counts, person, memory, now, scene, need="dropped")
        return None

    def _remote(self, person, counts, scene, memory, now) -> Guess | None:
        if self._quiet("remote", now):
            return None
        looking = counts.get("look_at_surfaces", 0) or counts.get("lift_or_move_things", 0)
        if not looking or not counts.get("press_remote_repeatedly", 0):
            return None
        spot = _spot(scene, memory, now, "remote")
        if spot is not None and spot.with_person:
            return None
        return self._located("remote", spot, counts, person, memory, now, scene, need="remote")

    def _lost(self, person, counts, scene, memory, now) -> Guess | None:
        del person, scene, memory
        if self._quiet("lost", now):
            return None
        if not (counts.get("stop_and_look_around", 0) and counts.get("turn_back", 0)):
            return None
        text = "Can I help you find something?"
        return Guess(
            "ask",
            "lost",
            speakable(text),
            ("Yes", "No", "I'm fine"),
            _chain(counts, "lost track", text),
        )

    def _located(self, name, spot, counts, person, memory, now, scene, need: str | None = None) -> Guess | None:
        del memory
        need = need or name
        if self._quiet(need, now):
            return None
        if spot is not None and spot.with_person:
            return None
        said = name.lower() in (scene.said or "").lower()
        score = _score(spot, said=said, weights=self.weights)
        text = _sentence(name, spot, now, person.facing, self.anchors)
        chain = _chain(counts, need, text)
        if spot is not None and score >= 0.6:
            return Guess("tell", need, text, (), chain)
        question = f"Are you looking for your {name}?"
        return Guess(
            "ask",
            need,
            speakable(question),
            (f"Your {name}", "Something else", "I'm fine"),
            _chain(counts, need, question),
        )


def place_phrase(
    surface: str,
    side: str,
    facing: str,
    left_anchor: str = "the window",
    right_anchor: str = "the door",
) -> str:
    """Where on a surface, in words that can follow 'is' or 'are'."""
    surface = (surface or "").strip() or "in the room"
    side = (side or "unclear").lower()
    if side in {"middle", "unclear"}:
        return surface
    bare = _bare(surface)
    if side == "front":
        return f"on the front edge of the {bare}"
    if side == "back":
        return f"on the back of the {bare}, near the wall"
    if side not in {"left", "right"}:
        return surface
    if facing == "camera":
        whose = "right" if side == "left" else "left"
        return f"on your {whose} side of the {bare}"
    if facing == "away":
        whose = "left" if side == "left" else "right"
        return f"on your {whose} side of the {bare}"
    end = "left side" if side == "left" else "right side"
    anchor = left_anchor if side == "left" else right_anchor
    return f"on the {end} of the {bare}, near {anchor}"


def speakable(text: str) -> str:
    """A complete sentence: no labels, no arrows, and an ending mark."""
    cleaned = " ".join(text.replace("_", " ").replace("→", " ").replace("/", " ").split())
    words = cleaned.split()
    if len(words) > 25:
        cleaned = " ".join(words[:25]).rstrip(".,?")
    if cleaned and cleaned[-1] not in ".?":
        cleaned += "."
    return cleaned


def _safety(need: str, counts: dict[str, int], pattern: str) -> Guess:
    text = "Are you okay?"
    return Guess("safety", need, text, ("Yes", "No"), _chain(counts, pattern, text), check=False)


def _gesture(kind: str, need: str, text: str, counts: dict[str, int], pattern: str) -> Guess:
    return Guess(kind, need, speakable(text), (), _chain(counts, pattern, text))


def _object(kind: str, need: str, text: str, counts: dict[str, int], person: PersonState) -> Guess:
    del person
    line = speakable(text)
    return Guess(kind, need, line, (), _chain(counts, need, line))


def _choice_question(names: list[str]) -> str:
    spoken = [f"your {name}" for name in names]
    if len(spoken) == 1:
        return f"Are you looking for {spoken[0]}?"
    if len(spoken) == 2:
        return f"Are you looking for {spoken[0]} or {spoken[1]}?"
    return f"Are you looking for {spoken[0]}, {spoken[1]}, or {spoken[2]}?"


def _choice_labels(names: list[str]) -> tuple[str, ...]:
    return tuple([*(f"Your {name}" for name in names), "Something else", "I'm fine"])


def _chain(counts: dict[str, int], pattern: str, said: str) -> str:
    if counts:
        seen = ", ".join(
            f"{label} ×{count}" if count > 1 else label for label, count in counts.items() if count
        )
    else:
        seen = "gesture"
    return f"saw: {seen} → pattern: {pattern} → said: {said}"


def _score(spot: "_Spot | None", *, said: bool, weights: dict[str, float]) -> float:
    score = weights["context"]
    if said:
        score += weights["said"]
    if spot is None:
        return score
    if not spot.with_person:
        score += weights["not_with_them"]
    if spot.fresh and not spot.unconfirmed:
        score += weights["in_view"]
    if spot.home:
        score += weights["usual_spot"]
    if spot.moved:
        score += weights["moved"]
    return score


def _sentence(name: str, spot: "_Spot | None", now: datetime, facing: str, anchors: tuple[str, str]) -> str:
    verb = "are" if name.endswith("s") else "is"
    if spot is None:
        return speakable(f"Are you looking for your {name}?")
    place = place_phrase(spot.location, spot.side, facing, anchors[0], anchors[1])
    if spot.at is None or now - spot.at > FRESH_MINUTES:
        usual = spot.home or place
        return speakable(f"Your {name} {verb} usually {usual}.")
    if now - spot.at > FRESH_NOW:
        return speakable(f"I last saw your {name} {place} a few minutes ago.")
    return speakable(f"Your {name} {verb} {place}.")


@dataclass
class _Spot:
    name: str
    location: str
    side: str
    at: datetime | None
    fresh: bool
    home: str | None
    with_person: bool
    near: bool
    on_head: bool
    unconfirmed: bool
    moved: bool


def _spot(scene: SceneState, memory: ObjectMemory, now: datetime, name: str) -> _Spot | None:
    for item in scene.objects_seen:
        if _same(item.name, name):
            return _from_sight(item, now)
    recall = memory.recall(name, now)
    if recall.status == "unknown":
        return None
    return _from_recall(recall)


def _drink_spot(scene: SceneState, memory: ObjectMemory, now: datetime) -> _Spot | None:
    for name in DRINKS:
        spot = _spot(scene, memory, now, name)
        if spot is not None:
            return spot
    return None


def _from_sight(item: ObjectSighting, now: datetime) -> _Spot:
    return _Spot(
        name=item.name.strip(),
        location=item.location_desc.strip() or "in the room",
        side=item.side,
        at=now,
        fresh=True,
        home=None,
        with_person=item.with_person,
        near=item.near_person,
        on_head=item.on_head,
        unconfirmed=False,
        moved=False,
    )


def _from_recall(recall: Recall) -> _Spot:
    return _Spot(
        name=recall.name,
        location=(recall.location_desc or recall.home_desc or "in the room"),
        side=recall.side,
        at=recall.last_seen_at,
        fresh=recall.status == "fresh",
        home=recall.home_desc,
        with_person=recall.with_person,
        near=recall.near_person,
        on_head=recall.on_head,
        unconfirmed=recall.unconfirmed,
        moved=bool(recall.home_desc and recall.location_desc and recall.home_desc != recall.location_desc),
    )


def _holding_drink(person: PersonState) -> bool:
    held = " ".join(person.holding).lower()
    return any(name in held for name in ("water", "cup", "glass", "mug", "bottle"))


def _same(left: str, right: str) -> bool:
    from fluffy.world.model import _object_key

    a = _object_key(left)
    b = _object_key(right)
    return bool(a) and a == b


def _bare(surface: str) -> str:
    text = surface.strip()
    for prefix in ("on the ", "on ", "by the ", "by ", "near the ", "near ", "at the ", "at "):
        if text.lower().startswith(prefix):
            return text[len(prefix) :].strip() or text
    return text


def _real_people(scene: SceneState) -> list[PersonState]:
    return [person for person in scene.people if not person.on_screen]


def rewrite(guess: Guess, text: str) -> Guess:
    line = speakable(text)
    chain = guess.chain
    if "→ said:" in chain:
        chain = chain.rsplit("→ said:", 1)[0] + f"→ said: {line}"
    return replace(guess, text=line, chain=chain)
