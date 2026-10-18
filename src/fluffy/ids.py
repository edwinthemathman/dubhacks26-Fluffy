"""Short opaque ids. Prefixes keep logs readable; values are not secrets."""

from __future__ import annotations

import secrets


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


class IdFactory:
    """Deterministic ids for inference helpers and their tests.

    Submitted v2 action and event ids use ``new_id`` instead. ``act_1`` does not
    satisfy the v2 action-id pattern.
    """

    def __init__(self) -> None:
        self._n = 0

    def __call__(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}_{self._n}"
