"""Run the instinct scene read on a folder of short clips.

Prints the action labels Perceptron returns, then the double-check questions.
Clips are not in the repo. Record them, then:

    uv run python scripts/label_clips.py clips/instincts
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fluffy.demo.analyze import analyze_clip  # noqa: E402
from fluffy.live.verify import questions_for  # noqa: E402
from fluffy.profile import load_profile  # noqa: E402

CLIP_LIST = (
    "glasses: touch face then shirt pocket",
    "look-alike: nose scratch",
    "look-alike: tired eye rub",
    "look-alike: adjusting glasses that are worn",
    "look-alike: brush crumbs off a shirt",
    "look-alike: stand up by pushing on the thighs",
    "look-alike: a pet walks by",
    "look-alike: a person on the TV",
    "water: rub the throat and cough",
    "look-alike: adjust a collar",
    "look-alike: one throat-clear",
    "safety: a pretend choking clutch, staged safely",
    "music: cup an ear while music plays",
    "look-alike: scratch an ear",
    "look-alike: a phone call",
    "music: a held open palm",
    "look-alike: a wave",
    "look-alike: reaching up to a shelf",
)


def main() -> None:
    load_dotenv(ROOT / ".env")
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "clips" / "instincts"
    print("Record these clips before trusting a pattern:")
    for line in CLIP_LIST:
        print(f"  - {line}")
    print("\nDouble-check questions:")
    for need in ("glasses", "water", "volume_up", "stop_music", "keys", "cane"):
        for question in questions_for(need):
            print(f"  {need}: {question.key} = {question.regex}")
    clips = sorted(folder.glob("*.mp4")) + sorted(folder.glob("*.webm")) + sorted(folder.glob("*.mov"))
    if not clips:
        print(f"\nNo clips in {folder}.")
        return
    key = os.environ.get("PERCEPTRON_API_KEY", "").strip()
    if not key:
        print("\nPERCEPTRON_API_KEY is not set, so the clips were not sent.")
        return
    profile = load_profile(ROOT / "config" / "profiles" / "elderly.toml")
    for clip in clips:
        print(f"\n== {clip.name}")
        try:
            result = analyze_clip(clip, profile, "", api_key=key)
        except Exception as exc:
            print(f"  failed: {exc}")
            continue
        scene = result.get("scene") if isinstance(result, dict) else None
        people = (scene or {}).get("people") if isinstance(scene, dict) else None
        if not people and isinstance(result, dict):
            print("  " + str(result.get("summary") or result.keys()))
            continue
        for person in people or []:
            actions = person.get("actions") or []
            labels = ", ".join(f"{item.get('label')} ×{item.get('count', 1)}" for item in actions) or "(none)"
            print(f"  actions: {labels}")
            print(f"  wearing_glasses={person.get('wearing_glasses')} zone={person.get('zone')} gesture={person.get('gesture')}")


if __name__ == "__main__":
    main()
