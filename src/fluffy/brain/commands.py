"""Fixed phrases Fluffy understands before the model sees them."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_WAKE = re.compile(r"^\s*(?:hey\s+)?(?:fluffy|fluff he|muffy)\b[,!. ]*", re.IGNORECASE)
_YES = re.compile(r"^\s*(yes|yeah|yep|okay|ok)\b", re.IGNORECASE)
_NO = re.compile(r"^\s*(no|nope|nah|i'm fine|im fine|i am fine)\b", re.IGNORECASE)
_REPEAT = re.compile(r"\b(repeat that|what did you say|say that again)\b", re.IGNORECASE)
_HELP = re.compile(
    r"\b(help|i fell|i can't get up|i cannot get up|call (?:my )?caregiver|call alex)\b",
    re.IGNORECASE,
)
_SCAN = re.compile(r"\b(what do you see|scan the room|look around)\b", re.IGNORECASE)
_FIND = re.compile(
    r"\b(?:where (?:is|are)(?: my)?|find(?: my)?|i can't find(?: my)?|i cannot find(?: my)?|have you seen(?: my)?)\s+(.+)",
    re.IGNORECASE,
)
_LIGHTS = re.compile(
    r"\b(lights off|turn(?: the)? lights off|lights on|turn(?: the)? lights on|dim the lights|dim lights|brighter|warmer)\b",
    re.IGNORECASE,
)
_MUSIC = re.compile(
    r"\b(stop the music|pause the music|stop music|pause music|play music|play (.+)|louder|quieter|volume up|volume down)\b",
    re.IGNORECASE,
)
_DOOR = re.compile(r"\b(open|close|shut)\s+(?:the\s+)?(front|bedroom)\s+door\b", re.IGNORECASE)
_TEXT = re.compile(r"\b(text|call)\s+([a-z][a-z .'-]{0,40})", re.IGNORECASE)


@dataclass(frozen=True)
class Command:
    name: str
    detail: str = ""
    args: dict = field(default_factory=dict)


def route(phrase: str, *, person_in_view: bool) -> Command | None:
    """A command, an ignore, or None when the model should decide.

    Without a wake word the person has to be in view, so a television does not
    drive the room. "Fluffy, ..." always counts.
    """
    raw = " ".join(phrase.strip().split())
    if not raw:
        return None
    woke = _WAKE.match(raw) is not None
    text = _WAKE.sub("", raw).strip() or raw
    if not woke and not person_in_view:
        return Command("ignore")
    matched = parse(text)
    if matched is not None:
        return matched
    return None


def parse(text: str) -> Command | None:
    lowered = text.strip()
    if not lowered:
        return None
    if lowered.lower() in {"stop", "never mind", "nevermind", "be quiet", "quiet"}:
        return Command("quiet")
    if _YES.match(lowered):
        return Command("yes", lowered)
    if _NO.match(lowered):
        return Command("no", lowered)
    if _REPEAT.search(lowered):
        return Command("repeat")
    found = _FIND.search(lowered)
    if found:
        item = _item(found.group(1))
        if item:
            return Command("find", item)
    if _HELP.search(lowered):
        return Command("help")
    if _SCAN.search(lowered):
        return Command("scan")
    lights = _LIGHTS.search(lowered)
    if lights:
        words = lights.group(1).lower()
        if "off" in words:
            return Command("lights", "off", {"luminosity": 0})
        if "dim" in words:
            return Command("lights", "dim", {"luminosity": 30})
        if "warmer" in words:
            return Command("warmth", "warm", {"warmth": "warm"})
        return Command("lights", "on", {"luminosity": 100})
    door = _DOOR.search(lowered)
    if door:
        verb, which = door.group(1).lower(), door.group(2).lower()
        name = "front_door" if which == "front" else "bedroom_door"
        tool = "close_door" if verb in {"close", "shut"} else "open_door"
        return Command("door", tool, {"door": name, "tool": tool})
    music = _MUSIC.search(lowered)
    if music:
        words = music.group(1).lower()
        if words.startswith("stop") or words.startswith("pause"):
            return Command("music", "stop")
        if "louder" in words or "up" in words:
            return Command("volume", "up")
        if "quieter" in words or "down" in words:
            return Command("volume", "down")
        title = (music.group(2) or "").strip(" .")
        if title and title != "music":
            return Command("music", title, {"query": title})
        return Command("music", "play")
    contact = _TEXT.search(lowered)
    if contact:
        kind = "call" if contact.group(1).lower() == "call" else "text"
        name = contact.group(2).strip(" .")
        return Command(kind, name, {"name": name})
    return None


def _item(raw: str) -> str:
    text = raw.strip(" .?!").lower()
    for tail in (" please", " for me"):
        if text.endswith(tail):
            text = text[: -len(tail)]
    return text
