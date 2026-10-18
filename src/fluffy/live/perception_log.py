"""One JSON line per Perceptron call. Frames stay out of the file."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG_PATH = Path("logs/perception.jsonl")


def record(
    *,
    lane: str,
    sent: dict[str, Any],
    raw: str | None,
    parsed: dict[str, Any],
    calls: list[dict[str, Any]],
    ms: int,
) -> None:
    row = {
        "at": datetime.now(timezone.utc).isoformat(),
        "lane": lane,
        "sent": sent,
        "raw": (raw or "")[:4000],
        "parsed": parsed,
        "calls": calls,
        "ms": ms,
    }
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, default=str) + "\n")
    except OSError:
        return
