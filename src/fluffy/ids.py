"""Short opaque ids. Prefixes keep logs readable; values are not secrets."""

from __future__ import annotations

import secrets


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"
