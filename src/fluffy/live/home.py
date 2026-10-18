"""A stand-in for the executor and mock home, so approved plans visibly change the room.

The real executor (Part 1) replaces `HomeState.apply` later. The shape matches
`HomeState` in web/src/stream/events.ts.
"""

from __future__ import annotations

import copy
from typing import Any


def initial_home() -> dict[str, Any]:
    return {
        "lamp": {"luminosity": 78, "warmth": "warm"},
        "ceiling": {"luminosity": 36},
        "blinds": 55,
        "door": {"front_door": "closed", "bedroom_door": "closed"},
        "speaker": {"sound": None, "volume": 0, "track": None},
        "phone": {"mode": None, "to": None, "preview": None, "delivered": False},
        "flash": {"active": False, "color": None, "pattern": None},
        "caption": None,
    }


class HomeState:
    def __init__(self) -> None:
        self.state = initial_home()
        self.flash_token = 0

    def reset(self) -> dict[str, Any]:
        self.flash_token += 1
        self.state = initial_home()
        return {"type": "state", **copy.deepcopy(self.state)}

    def apply(self, tool: str, args: dict[str, Any], result: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Apply one tool call. Returns a `state` event, or None if nothing in the room changes."""
        s = self.state
        result = result or {}
        if tool == "dim_lights":
            level = int(args.get("luminosity", 50))
            light = args.get("light")
            if light in (None, "lamp.living_floor"):
                s["lamp"]["luminosity"] = level
            if light in (None, "light.living_ceiling"):
                s["ceiling"]["luminosity"] = level
            return {"type": "state", "lamp": dict(s["lamp"]), "ceiling": dict(s["ceiling"])}
        if tool == "set_light_warmth":
            s["lamp"]["warmth"] = args.get("warmth", "neutral")
            return {"type": "state", "lamp": dict(s["lamp"])}
        if tool == "set_blinds":
            s["blinds"] = int(args.get("position", 50))
            return {"type": "state", "blinds": s["blinds"]}
        if tool == "play_sound":
            s["speaker"] = {"sound": args.get("sound"), "volume": int(args.get("volume", 40)), "track": None}
            return {"type": "state", "speaker": dict(s["speaker"])}
        if tool == "stop_sound":
            s["speaker"] = {"sound": None, "volume": 0, "track": None}
            return {"type": "state", "speaker": dict(s["speaker"])}
        if tool in {"open_door", "close_door"}:
            door = str(args.get("door") or "front_door")
            if door not in s["door"]:
                return None
            s["door"][door] = "open" if tool == "open_door" else "closed"
            return {"type": "state", "door": dict(s["door"])}
        if tool in {"play_music", "spotify_play"}:
            label = None
            if result.get("track"):
                artist = result.get("artist") or ""
                label = f"{result['track']} — {artist}".strip(" —")
            s["speaker"] = {
                "sound": "music",
                "volume": int(args.get("volume", s["speaker"].get("volume") or 40)),
                "track": label,
            }
            return {"type": "state", "speaker": dict(s["speaker"])}
        if tool in {"stop_music", "spotify_pause"}:
            s["speaker"] = {"sound": None, "volume": 0, "track": None}
            return {"type": "state", "speaker": dict(s["speaker"])}
        if tool in {"send_text", "alert_caregiver"}:
            s["phone"] = {
                "mode": "text",
                "to": result.get("to") or args.get("to") or "caregiver",
                "preview": str(args.get("message") or "")[:80] or None,
                "delivered": bool(result.get("delivered")),
            }
            return {"type": "state", "phone": dict(s["phone"])}
        if tool == "call_person":
            s["phone"] = {
                "mode": "call (demo)",
                "to": result.get("to") or args.get("to"),
                "preview": None,
                "delivered": False,
            }
            return {"type": "state", "phone": dict(s["phone"])}
        if tool == "flash_lights":
            self.flash_token += 1
            s["flash"] = {"active": True, "color": args.get("color"), "pattern": args.get("pattern")}
            return {"type": "state", "flash": dict(s["flash"])}
        if tool == "show_caption":
            s["caption"] = args.get("text")
            return {"type": "state", "caption": s["caption"]}
        return None

    def end_flash(self, token: int) -> dict[str, Any] | None:
        """Turn the flash off only if `token` is still the one that turned it on."""
        if token != self.flash_token:
            return None
        self.state["flash"] = {"active": False, "color": None, "pattern": None}
        return {"type": "state", "flash": dict(self.state["flash"])}

    def clear_caption(self) -> dict[str, Any]:
        self.state["caption"] = None
        return {"type": "state", "caption": None}
