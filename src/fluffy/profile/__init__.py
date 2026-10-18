"""Load `config/profiles/*.toml`."""

import tomllib
from pathlib import Path

from fluffy.profile.model import Profile


def load_profile(path: Path) -> Profile:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    return Profile.model_validate(data)


def load_profiles(directory: Path) -> dict[str, Profile]:
    profiles: dict[str, Profile] = {}
    for path in sorted(directory.glob("*.toml")):
        profile = load_profile(path)
        profiles[profile.id] = profile
    return profiles
