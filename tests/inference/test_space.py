"""Boxes become a side of a surface, or nothing when the item is missing."""

from fluffy.perception.space import Box, boxes_from_detections, place_boxes


def test_a_bottle_on_the_right_third_of_the_desk():
    desk = Box("desk", 0, 100, 300, 80)
    bottle = Box("water bottle", 220, 40, 30, 70)
    placed = place_boxes(bottle, [desk])
    assert placed is not None
    assert placed.surface == "desk"
    assert placed.side == "right"


def test_an_item_outside_every_surface_uses_the_nearest_one_below():
    table = Box("table", 0, 200, 100, 40)
    sofa = Box("sofa", 400, 200, 100, 40)
    glasses = Box("glasses", 20, 10, 20, 10)
    placed = place_boxes(glasses, [sofa, table])
    assert placed is not None
    assert placed.surface == "table"


def test_no_item_box_is_not_a_guess():
    rows = [{"class": "desk", "box": {"x": 0, "y": 0, "w": 100, "h": 40}}]
    assert boxes_from_detections(rows, item="water bottle", surfaces=["desk"]) is None
