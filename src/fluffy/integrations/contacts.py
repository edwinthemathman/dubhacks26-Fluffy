"""Named people Fluffy can text or call. Handles may be `$ENV_VAR`."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CONTACTS = ROOT / "config" / "contacts.toml"

_cache: dict[str, dict[str, str]] | None = None


def _load() -> dict[str, dict[str, str]]:
    global _cache
    if _cache is not None:
        return _cache
    if not CONTACTS.is_file():
        _cache = {}
        return _cache
    raw = tomllib.loads(CONTACTS.read_text())
    people = raw.get("contacts") if isinstance(raw, dict) else None
    loaded: dict[str, dict[str, str]] = {}
    if isinstance(people, dict):
        for key, value in people.items():
            if isinstance(value, dict):
                loaded[str(key)] = {str(k): str(v) for k, v in value.items()}
    _cache = loaded
    return _cache


def _expand(value: str) -> str:
    text = value.strip()
    if text.startswith("$"):
        return os.environ.get(text[1:], "").strip()
    return text


def contact_name(contact_id: str) -> str:
    person = _load().get(contact_id) or {}
    return person.get("name") or contact_id


def resolve_handle(contact_id: str) -> str:
    person = _load().get(contact_id) or {}
    return _expand(person.get("handle", ""))
