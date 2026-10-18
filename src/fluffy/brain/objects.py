"""Object notices. No model calls: the same idea as the cue rules.

A room scan sets where something usually lives. Live reads update where it
was last seen. These rules turn that memory into one short thing to say.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from fluffy.brain.cues import derive
from fluffy.perception.scene import SceneState
from fluffy.profile.model import Profile
from fluffy.world.model import ObjectMemory, Recall, Tag, _object_key

IMPORTANT_DEFAULT = {
    "wallet",
    "keys",
    "phone",
    "glasses",
    "hearing aids",
    "cane",
    "walker",
    "pill organizer",
}
MOBILITY = {"cane", "walker", "phone"}
COOLDOWN = timedelta(minutes=2)
RECENT = timedelta(minutes=2)
_LEAVE_WORDS = ("walks out", "walks off", "leaves the", "left the room")


@dataclass(frozen=True)
class ObjectNotice:
    kind: str
    name: str
    location: str
    text: str
    choices: tuple[str, ...] = ()
    chain: str = ""


def important_for(profile: Profile) -> set[str]:
    named = {_object_key(item) for item in profile.important_objects if item.strip()}
    return named or set(IMPORTANT_DEFAULT)


def tag_lines(memory: ObjectMemory, now: datetime, profile: Profile) -> list[str]:
    lines = []
    for tag in memory.tags(now, important_for(profile)):
        flags = []
        if tag.with_person:
            flags.append("with them")
        elif tag.near_person:
            flags.append("near them")
        else:
            flags.append("not with them")
        if tag.on_floor:
            flags.append("on the floor")
        if tag.unconfirmed:
            flags.append("unconfirmed")
        age = max(0, int((now - tag.last_seen_at).total_seconds() // 60))
        lines.append(f"{tag.name}, {tag.location_desc}, {', '.join(flags)}, {age} min ago")
    return lines


def find_line(recall: Recall, now: datetime) -> str:
    if recall.status == "unknown" or recall.last_seen_at is None:
        return f"There is no saved sighting of {recall.name}. Look in the video."
    if recall.status == "stale" and recall.home_desc:
        return f"{recall.name}: usually {recall.home_desc}"
    age = max(0, int((now - recall.last_seen_at).total_seconds() // 60))
    carried = "with them" if recall.with_person else "not with them"
    return f"{recall.name}: {recall.location_desc}, seen {age} min ago, {carried}"


def wants_room_scan(
    *,
    empty_for_s: float,
    entered: bool,
    said: str,
    already_scanned_empty: bool,
) -> str | None:
    if "scan the room" in (said or "").lower():
        return "scan the room"
    if entered:
        return "entered_room"
    if empty_for_s >= 60 and not already_scanned_empty:
        return "empty"
    return None


def object_notices(
    scene: SceneState | None,
    memory: ObjectMemory,
    profile: Profile,
    now: datetime,
    *,
    departing: bool = False,
    previous_floor: dict[str, bool] | None = None,
    fired: dict[str, datetime] | None = None,
    skip_search: bool = False,
) -> list[ObjectNotice]:
    if scene is None or "fall" in derive(scene):
        return []
    important = important_for(profile)
    found: list[ObjectNotice] = []
    # Action labels are read by instincts. The old searching flag is only a fallback.
    if not skip_search:
        found.extend(_searching(scene, memory, profile, now, important, fired))
    if departing or _clip_left(scene):
        found.extend(_left_behind(scene, memory, profile, now, important, fired))
    found.extend(_dropped(scene, memory, profile, now, important, previous_floor or {}, fired))
    found.extend(_out_of_reach(scene, memory, profile, now, important, fired))
    return found


def _searching(
    scene: SceneState,
    memory: ObjectMemory,
    profile: Profile,
    now: datetime,
    important: set[str],
    fired: dict[str, datetime] | None,
) -> list[ObjectNotice]:
    person = next((item for item in scene.people if item.searching), None)
    if person is None:
        return []
    tags = memory.tags(now, important)
    if person.looking_for:
        match = _match(person.looking_for, tags)
        if match is not None and _cool("where", match.name, now, fired):
            return [
                ObjectNotice(
                    "where",
                    match.name,
                    match.location_desc,
                    _say(profile, "where", match.name, match.location_desc),
                )
            ]
    names = [tag.name for tag in tags if not tag.with_person][:4]
    if not names or not _cool("searching", "room", now, fired):
        return []
    return [
        ObjectNotice(
            "searching",
            names[0],
            "",
            "Which are you looking for?",
            tuple([*names, "Something else"]),
        )
    ]


def _left_behind(
    scene: SceneState,
    memory: ObjectMemory,
    profile: Profile,
    now: datetime,
    important: set[str],
    fired: dict[str, datetime] | None,
) -> list[ObjectNotice]:
    visible = {_object_key(item.name): item for item in scene.objects_seen}
    notices = []
    for tag in memory.tags(now, important):
        key = _object_key(tag.name)
        seen = visible.get(key)
        if key not in important or seen is None or seen.with_person or tag.with_person:
            continue
        if now - tag.last_seen_at > RECENT:
            continue
        if not _cool("left_behind", tag.name, now, fired):
            continue
        notices.append(
            ObjectNotice(
                "left_behind",
                tag.name,
                tag.location_desc,
                _say(profile, "left_behind", tag.name, tag.location_desc),
            )
        )
    return notices


def _dropped(
    scene: SceneState,
    memory: ObjectMemory,
    profile: Profile,
    now: datetime,
    important: set[str],
    previous_floor: dict[str, bool],
    fired: dict[str, datetime] | None,
) -> list[ObjectNotice]:
    notices = []
    for item in scene.objects_seen:
        key = _object_key(item.name)
        if key not in important or not item.on_floor or previous_floor.get(key, False):
            continue
        if key not in previous_floor and not _timeline_drop(scene, item.name):
            continue
        if not _cool("dropped", item.name, now, fired):
            continue
        place = item.location_desc.strip() or "on the floor"
        notices.append(
            ObjectNotice(
                "dropped",
                item.name.strip(),
                place,
                _say(profile, "dropped", item.name.strip(), place),
            )
        )
    return notices


def _out_of_reach(
    scene: SceneState,
    memory: ObjectMemory,
    profile: Profile,
    now: datetime,
    important: set[str],
    fired: dict[str, datetime] | None,
) -> list[ObjectNotice]:
    seated = any(person.posture in {"sitting", "lying"} for person in scene.people)
    if not seated:
        return []
    visible = {_object_key(item.name): item for item in scene.objects_seen}
    notices = []
    for tag in memory.tags(now, important):
        key = _object_key(tag.name)
        seen = visible.get(key)
        if key not in MOBILITY or seen is None or seen.near_person or seen.with_person:
            continue
        if not _cool("out_of_reach", tag.name, now, fired):
            continue
        notices.append(
            ObjectNotice(
                "out_of_reach",
                tag.name,
                tag.location_desc,
                _say(
                    profile,
                    "out_of_reach",
                    tag.name,
                    tag.location_desc,
                    profile.agreements.caregiver,
                ),
            )
        )
    return notices


def _say(
    profile: Profile,
    kind: str,
    name: str,
    location: str,
    caregiver: str = "someone",
) -> str:
    short = profile.id == "speech_cognitive" or "simple_language" in profile.needs
    if kind == "left_behind":
        return f"{name.capitalize()} is still there." if short else f"Your {name} is still {location}."
    if kind == "dropped":
        return f"Your {name} fell." if short else f"Your {name} fell, it's {location}."
    if kind == "out_of_reach":
        if short:
            return f"Your {name} is {location}. Should I tell you where?"
        return f"Your {name} is {location}. Want me to tell {caregiver}?"
    verb = "are" if name.endswith("s") else "is"
    return f"Your {name} {verb} {location}."


def _match(wanted: str, tags: list[Tag]) -> Tag | None:
    key = _object_key(wanted)
    if not key:
        return None
    for tag in tags:
        stored = _object_key(tag.name)
        if key == stored or key in stored or stored in key:
            return tag
    return None


def _cool(kind: str, name: str, now: datetime, fired: dict[str, datetime] | None) -> bool:
    if fired is None:
        return True
    key = f"{kind}:{_object_key(name)}"
    last = fired.get(key)
    if last is not None and now - last < COOLDOWN:
        return False
    fired[key] = now
    return True


def _clip_left(scene: SceneState) -> bool:
    if any(person.activity == "leaving" for person in scene.people):
        return True
    text = " ".join(scene.timeline).lower()
    return any(word in text for word in _LEAVE_WORDS)


def _timeline_drop(scene: SceneState, name: str) -> bool:
    text = " ".join(scene.timeline).lower()
    return _object_key(name) in text and any(word in text for word in ("fell", "falls", "dropped", "drops"))
