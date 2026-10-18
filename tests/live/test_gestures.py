"""Stop palm and cupped ear follow the music that is actually playing."""

from fluffy.live.loop import LiveLoop
from fluffy.perception.scene import PersonState, SceneState

from tests.inference.helpers import profiles


def _loop(**kwargs):
    events: list[dict] = []
    loop = LiveLoop(object(), profiles(), profile_id="blind", emit=events.append, **kwargs)
    return loop, events


def test_two_stop_palms_pause_only_while_music_plays():
    paused: list[dict] = []
    loop, _events = _loop(on_pause=lambda: paused.append({"ok": True}) or {"ok": True})
    assert loop.note_gesture("stop", agreed=True) is None
    loop.home.apply("play_music", {"query": "song"})
    assert loop._music_on()
    assert loop.note_gesture("stop") is None
    assert paused == []
    assert loop.note_gesture("stop") == "stop"
    assert paused


def test_a_failed_pause_says_so():
    loop, events = _loop(on_pause=lambda: {"ok": False, "error": "nope"})
    loop.home.apply("spotify_play", {"query": "song"})
    loop.note_gesture("stop", agreed=True)
    said = [event.get("text") for event in events if event.get("type") == "say"]
    assert "I couldn't reach Spotify." in said
    assert "Okay, I stopped the music." not in said


def test_cupping_an_ear_raises_the_volume_once():
    loop, _events = _loop(on_volume=lambda level: {"ok": True, "volume": level})
    loop.home.apply("play_music", {"query": "song", "volume": 40})
    assert loop.note_gesture("cup_ear") is None
    assert loop.note_gesture("cup_ear") == "louder"
    assert loop.home.state["speaker"]["volume"] == 60
    assert loop.note_gesture("cup_ear", agreed=True) is None
    assert loop.home.state["speaker"]["volume"] == 60


def test_a_phone_at_the_ear_is_not_a_volume_gesture():
    loop, _events = _loop()
    loop.home.apply("play_music", {"query": "song", "volume": 40})
    loop.scene = SceneState(people=[PersonState(posture="standing", on_phone="yes")])
    assert loop.note_gesture("cup_ear", agreed=True) is None
    assert loop.home.state["speaker"]["volume"] == 40
