"""Which side of which surface an object sits on, from detection boxes."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Box:
    name: str
    x: float
    y: float
    w: float
    h: float


@dataclass(frozen=True)
class Placement:
    item: str
    surface: str
    side: str
    depth: str
    box: Box


def load_surfaces(path: Path | None = None) -> list[str]:
    file = path or (_ROOT / "config" / "home.toml")
    names = ["table", "desk", "side table", "coffee table", "counter", "sofa", "nightstand"]
    if not file.is_file():
        return names
    with file.open("rb") as handle:
        data = tomllib.load(handle)
    found = data.get("surfaces")
    if isinstance(found, list) and found:
        named = [str(row.get("name")).strip() for row in found if isinstance(row, dict) and row.get("name")]
        if named:
            return named
    return names


def place_boxes(item: Box, surfaces: list[Box]) -> Placement | None:
    """Put `item` on the surface that holds its bottom-centre, or the nearest one below."""
    if item.w <= 0 or item.h <= 0 or not surfaces:
        return None
    cx = item.x + item.w / 2
    bottom = item.y + item.h
    holders = [
        surface
        for surface in surfaces
        if surface.w > 0
        and surface.h > 0
        and surface.x <= cx <= surface.x + surface.w
        and surface.y <= bottom <= surface.y + surface.h
    ]
    if holders:
        surface = min(holders, key=lambda box: box.w * box.h)
    else:
        below = [surface for surface in surfaces if surface.y + surface.h >= bottom and surface.w > 0]
        pool = below or [surface for surface in surfaces if surface.w > 0 and surface.h > 0]
        if not pool:
            return None
        surface = min(pool, key=lambda box: abs((box.x + box.w / 2) - cx) + abs(box.y - bottom))
    return Placement(
        item=item.name,
        surface=surface.name,
        side=_third(cx, surface),
        depth=_depth(item.y + item.h / 2, surface),
        box=item,
    )


def boxes_from_detections(rows: list[dict], *, item: str, surfaces: list[str]) -> Placement | None:
    """Read Perceptron detect rows. A missing item is None, never a guess."""
    wanted_item = item.strip().lower()
    wanted_surfaces = {name.strip().lower() for name in surfaces}
    item_box: Box | None = None
    surface_boxes: list[Box] = []
    for row in rows:
        name = str(row.get("class") or row.get("label") or row.get("name") or "").strip()
        box = _box(name, row)
        if box is None:
            continue
        key = name.lower()
        if item_box is None and (key == wanted_item or wanted_item in key):
            item_box = box
        elif key in wanted_surfaces:
            surface_boxes.append(box)
    if item_box is None:
        return None
    return place_boxes(item_box, surface_boxes)


def _box(name: str, row: dict) -> Box | None:
    raw = row.get("box") or row.get("bbox") or row
    if not isinstance(raw, dict):
        return None
    try:
        x = float(raw.get("x", raw.get("left")))
        y = float(raw.get("y", raw.get("top")))
        w = float(raw.get("w", raw.get("width")))
        h = float(raw.get("h", raw.get("height")))
    except (TypeError, ValueError):
        return None
    return Box(name=name or "object", x=x, y=y, w=w, h=h)


def _third(cx: float, surface: Box) -> str:
    rel = (cx - surface.x) / surface.w
    if rel < 1 / 3:
        return "left"
    if rel > 2 / 3:
        return "right"
    return "middle"


def _depth(cy: float, surface: Box) -> str:
    rel = (cy - surface.y) / surface.h
    if rel < 1 / 3:
        return "back"
    if rel > 2 / 3:
        return "front"
    return "middle"
