"""Write the frozen plan, tool, and scene schemas."""

from __future__ import annotations

import json
from pathlib import Path

from fluffy.config import HomeConfig
from fluffy.models.plan import wire_schema
from fluffy.models.scene import scene_schema
from fluffy.tools.registry import configure


def export_schemas(directory: Path, home: HomeConfig) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    bound = configure(home)
    documents = {
        "plan.schema.json": wire_schema(),
        "tool.schema.json": bound.manifest(),
        "scene.schema.json": scene_schema(),
    }
    written: list[Path] = []
    for name, document in documents.items():
        path = directory / name
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        written.append(path)
    return written
