"""Score object tags against a fixed layout. Without --live it only prints the checklist.

Ground truth for a live run:
  glasses on the left of the side table
  keys on the right of the counter
  phone in hand
  cup on the coffee table
  remote on the sofa

A live run takes 10 scene reads and 3 scans, then reports how often each object
was found, in the right place, and whether with_person was right.
"""

from __future__ import annotations

import argparse

TRUTH = (
    ("glasses", "side table", "left", False),
    ("keys", "counter", "right", False),
    ("phone", "in hand", None, True),
    ("cup", "coffee table", None, False),
    ("remote", "sofa", None, False),
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="use the camera; not run from here")
    args = parser.parse_args()
    if args.live:
        raise SystemExit("Live scoring needs the camera session. Run it from the demo machine.")
    print("Object check (no camera). Place these, then run with --live on the demo machine:")
    for name, place, side, held in TRUTH:
        where = f"{place}, {side}" if side else place
        print(f"  {name}: {where}" + (" (in hand)" if held else ""))


if __name__ == "__main__":
    main()
