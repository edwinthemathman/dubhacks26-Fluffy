"""Simulated effects: brightness, flash overlays, and caption identity."""

from datetime import datetime, timedelta, timezone

from fluffy.config import IntegrationsConfig, load_home_config, repo_root
from fluffy.tools.registry import build_registry
from mockhome.state import build_store

NOW = datetime(2026, 10, 18, 1, 0, tzinfo=timezone.utc)


def _store(tmp_path, now_box):
    home = load_home_config(repo_root() / "config" / "home.toml", {})
    registry = build_registry(home)
    return build_store(
        home,
        registry,
        IntegrationsConfig(),
        tmp_path,
        repo_root(),
        lambda: now_box[0],
    )


def test_open_door_commits_and_play_music_does_not_pretend(tmp_path):
    now_box = [NOW]
    store = _store(tmp_path, now_box)
    opened = store.apply("open_door", {"door": "front_door"}, "ses_demo", "pln_1", "act_door_0001")
    assert opened.status == "succeeded"
    assert opened.verification == "state_committed"
    assert store.doors["front_door"] == "open"
    assert store.doors["bedroom_door"] == "closed"
    music = store.apply("play_music", {"query": "some jazz"}, "ses_demo", "pln_1", "act_music_0001")
    assert music.status == "failed"
    assert music.error_code == "spotify_needs_authorization"
    closed = store.apply("close_door", {"door": "front_door"}, "ses_demo", "pln_1", "act_door_0002")
    assert closed.status == "succeeded"
    assert store.doors["front_door"] == "closed"


def test_dim_and_warmth_and_blinds_commit(tmp_path):
    now_box = [NOW]
    store = _store(tmp_path, now_box)
    dim = store.apply(
        "dim_lights",
        {"room": "living_room", "luminosity": 35, "light": "lamp.living_floor"},
        "ses_demo",
        "pln_1",
        "act_dim_0001",
    )
    assert dim.status == "succeeded"
    assert dim.verification == "state_committed"
    warmth = store.apply(
        "set_light_warmth",
        {"room": "living_room", "warmth": "warm", "light": "lamp.living_floor"},
        "ses_demo",
        "pln_1",
        "act_warm_0001",
    )
    blinds = store.apply(
        "set_blinds",
        {"room": "living_room", "position": 0},
        "ses_demo",
        "pln_1",
        "act_blind_0001",
    )
    state = store.snapshot()
    floor = next(light for light in state.lights if light.id == "lamp.living_floor")
    ceiling = next(light for light in state.lights if light.id == "light.living_ceiling")
    assert floor.luminosity == 35
    assert floor.color == "#FFD6A0"
    assert ceiling.luminosity == 80
    assert warmth.status == "succeeded"
    assert state.blinds[0].position == 0
    assert blinds.verification == "state_committed"


def test_flash_does_not_restore_over_a_newer_brightness(tmp_path):
    now_box = [NOW]
    store = _store(tmp_path, now_box)
    store.apply(
        "dim_lights",
        {"room": "living_room", "luminosity": 40, "light": "lamp.living_floor"},
        "ses_demo",
        "pln_1",
        "act_dim_0001",
    )
    flash = store.apply(
        "flash_lights",
        {"room": "living_room", "color": "blue", "pulses": 2},
        "ses_demo",
        "pln_1",
        "act_flash_0001",
    )
    assert flash.new_state["luminosity"]["lamp.living_floor"] == 40
    store.apply(
        "dim_lights",
        {"room": "living_room", "luminosity": 10, "light": "lamp.living_floor"},
        "ses_demo",
        "pln_2",
        "act_dim_0002",
    )
    now_box[0] = NOW + timedelta(seconds=3)
    state = store.snapshot()
    floor = next(light for light in state.lights if light.id == "lamp.living_floor")
    assert floor.luminosity == 10
    assert state.flashes == []


def test_captions_expire_by_id_and_high_priority_stays(tmp_path):
    now_box = [NOW]
    store = _store(tmp_path, now_box)
    store.apply(
        "show_caption",
        {"text": "First note", "priority": "normal", "ttl_s": 3},
        "ses_demo",
        "pln_1",
        "act_cap_00001",
    )
    store.apply(
        "show_caption",
        {"text": "Please respond", "priority": "high", "ttl_s": 30},
        "ses_demo",
        "pln_1",
        "act_cap_00002",
    )
    now_box[0] = NOW + timedelta(seconds=1)
    store.apply(
        "show_caption",
        {"text": "Newer note", "priority": "normal", "ttl_s": 10},
        "ses_demo",
        "pln_1",
        "act_cap_00003",
    )
    now_box[0] = NOW + timedelta(seconds=4)
    state = store.snapshot()
    texts = [caption.text for caption in state.captions]
    assert "First note" not in texts
    assert "Please respond" in texts
    assert "Newer note" in texts
    store.dismiss_caption("act_cap_00002")
    remaining = [caption.text for caption in store.snapshot().captions]
    assert "Please respond" not in remaining
    assert "Newer note" in remaining


def test_announce_without_elevenlabs_is_not_playback_success(tmp_path):
    now_box = [NOW]
    store = _store(tmp_path, now_box)
    effect = store.apply(
        "announce",
        {"text": "Music is playing.", "room": "living_room"},
        "ses_demo",
        "pln_1",
        "act_say_00001",
    )
    assert effect.status == "failed"
    assert effect.verification == "unverified"
    assert effect.error_code == "elevenlabs_not_configured"
    assert effect.message is not None
    assert effect.message.text == "Music is playing."
    assert any(caption.text == "Music is playing." for caption in store.snapshot().captions)


def test_missing_spotify_does_not_pretend_to_play(tmp_path):
    now_box = [NOW]
    store = _store(tmp_path, now_box)
    effect = store.apply(
        "play_sound",
        {"room": "living_room", "sound": "arrival_song", "volume": 35},
        "ses_demo",
        "pln_1",
        "act_play_0001",
    )
    assert effect.status == "failed"
    assert effect.effect_kind == "spotify"
    assert effect.verification == "unverified"
    assert effect.error_code == "spotify_not_configured"
    media = store.snapshot().media[0]
    assert media.playback == "error"
    assert media.playback != "playing"
