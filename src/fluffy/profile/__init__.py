"""Load `config/profiles/*.toml`."""

import tomllib
from pathlib import Path

from fluffy.profile.model import Profile


def load_profile(path: Path) -> Profile:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    names = data.pop("objects", None)
    profile = Profile.model_validate(data)
    if isinstance(names, dict):
        profile.object_names = {str(key): str(value) for key, value in names.items()}
    return profile


def load_profiles(directory: Path) -> dict[str, Profile]:
    profiles: dict[str, Profile] = {}
    for path in sorted(directory.glob("*.toml")):
        profile = load_profile(path)
        profiles[profile.id] = profile
    return profiles
