"""Short deterministic ids for plans, events, and actions."""


class IdFactory:
    def __init__(self) -> None:
        self._n = 0

    def __call__(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}_{self._n}"
