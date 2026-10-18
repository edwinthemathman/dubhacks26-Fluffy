"""Offline compatibility for main device helpers.

The live loop no longer calls this. The v2 registry and executor own effects.
"""

from __future__ import annotations

from typing import Any

from fluffy.integrations.contacts import contact_name, resolve_handle
from fluffy.integrations.spotify_legacy import Spotify
from fluffy.notify.photon import notify_fall, shared_sms

_HOME = {
    "dim_lights",
    "set_light_warmth",
    "set_blinds",
    "play_sound",
    "stop_sound",
    "flash_lights",
    "show_caption",
    "announce",
    "ask_user",
}


def run_tool(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    if tool == "play_music":
        return Spotify().play(args.get("query"), args.get("volume"))
    if tool == "stop_music":
        return Spotify().stop()
    if tool in {"open_door", "close_door"}:
        return {"ok": True, "door": args.get("door"), "state": "open" if tool == "open_door" else "closed"}
    if tool == "call_person":
        name = contact_name(str(args.get("to") or "")) or "them"
        return {"ok": True, "label": "call (demo)", "to": name}
    if tool == "send_text":
        return _text(str(args.get("to") or ""), str(args.get("message") or ""))
    if tool == "alert_caregiver":
        message = args.get("message")
        delivered = notify_fall(message if isinstance(message, str) else None)
        if not delivered:
            return {"ok": False, "delivered": False, "error": "texting unavailable"}
        return {"ok": True, "delivered": True, "to": "caregiver"}
    if tool in _HOME:
        return {"ok": True}
    return {"ok": False, "error": f"unknown tool {tool}"}


def _text(contact_id: str, message: str) -> dict[str, Any]:
    handle = resolve_handle(contact_id)
    if not handle:
        return {"ok": False, "delivered": False, "error": "texting unavailable"}
    sms = shared_sms()
    if not sms.configured:
        return {"ok": False, "delivered": False, "error": "texting unavailable"}
    try:
        sms.send_text(handle, message)
    except Exception as exc:
        return {"ok": False, "delivered": False, "error": str(exc)[:80] or "texting unavailable"}
    return {"ok": True, "delivered": True, "to": contact_id}
