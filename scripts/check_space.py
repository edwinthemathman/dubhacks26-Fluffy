"""Print which side of a surface a box lands on.

Pass nothing to check the built-in desk example. A live still can be added
later; this script does not call Perceptron.
"""

from fluffy.perception.space import Box, place_boxes


def main() -> None:
    desk = Box("desk", 0, 100, 300, 80)
    bottle = Box("water bottle", 230, 40, 28, 70)
    glasses = Box("glasses", 30, 70, 24, 16)
    for item in (bottle, glasses):
        placed = place_boxes(item, [desk, Box("table", 0, 220, 180, 40)])
        if placed is None:
            print(f"{item.name}: not in frame")
        else:
            print(f"{item.name}: {placed.surface}, {placed.side}, {placed.depth}")


if __name__ == "__main__":
    main()
