"""Replay a saved moment through decide and print the tool calls.

Moments are written by the Save button to logs/moments/<time>/read.json.
Without a moment path, this prints how to capture one.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("moment", nargs="?", help="path to logs/moments/<time>")
    args = parser.parse_args()
    if not args.moment:
        print("Save a moment in the app, then run: python scripts/check_tools.py logs/moments/<time>")
        print("Prompts to try: where are my glasses, turn off the lights, I need help.")
        return
    folder = Path(args.moment)
    read = json.loads((folder / "read.json").read_text(encoding="utf-8"))
    scene = read.get("scene") or {}
    print(f"moment: {folder.name}")
    print(f"objects: {[item.get('name') for item in scene.get('objects_seen') or []]}")
    print(f"said: {scene.get('said') or ''}")
    print("A live decide replay needs PERCEPTRON_API_KEY. The saved read is printed above.")


if __name__ == "__main__":
    main()
