"""The live loop acts on an instinct, and a failed check stays quiet."""

import asyncio

from fluffy.perception.scene import ObjectSighting, PersonAction, PersonState, SceneState
from tests.live.test_live_loop import make


def _scene():
    return SceneState(
        people=[
            PersonState(
                posture="standing",
                face_visible="yes",
                wearing_glasses="no",
                on_phone="no",
                speaking="no",
                actions=[PersonAction(label="cup_ear", count=2)],
            )
        ]
    )


def test_cup_ear_raises_the_volume_by_twenty_once():
    seen: list[int] = []

    def on_volume(level: int) -> dict:
        seen.append(level)
        return {"ok": True, "volume": level}

    loop, _, clock, events = make(on_volume=on_volume)
    loop.home.state["speaker"]["sound"] = "song"
    loop.home.state["speaker"]["volume"] = 40
    scene = _scene()
    now = clock.now()
    loop.instincts.note(scene, now)
    guess = loop.instincts.guess(
        scene,
        loop.world.objects,
        loop.profile,
        now,
        music_on=True,
        spoke_recently=False,
        last_line="",
    )
    assert guess is not None
    loop._perform_guess(guess, now)
    assert loop.home.state["speaker"]["volume"] == 60
    assert seen == [60]
    assert any(event.get("text") == "Turning the music up." for event in events)
    loop.instincts.note(scene, now)
    assert (
        loop.instincts.guess(
            scene,
            loop.world.objects,
            loop.profile,
            now,
            music_on=True,
            spoke_recently=False,
            last_line="",
        )
        is None
    )


def test_a_failed_check_does_not_speak():
    loop, fake, clock, events = make()

    async def check(window, questions):
        del window, questions
        return {"wearing": "yes", "on_head": "no", "looking": "other", "where": "none"}

    fake.check = check
    scene = SceneState(
        people=[
            PersonState(
                posture="standing",
                face_visible="yes",
                wearing_glasses="no",
                actions=[
                    PersonAction(label="touch_face_eyes", count=1),
                    PersonAction(label="pat_chest_pocket", count=1),
                ],
            )
        ],
        objects_seen=[ObjectSighting(name="glasses", location_desc="on the side table")],
    )
    loop.world.objects.note_live(scene.objects_seen, clock.now())

    class Window:
        frames = [(b"a", 0), (b"b", 1)]

    asyncio.run(loop._handle_notices(scene, set(), Window()))
    assert not any(event.get("type") == "say" for event in events)
    assert any(event.get("note") == "not sure, staying quiet" for event in events)


def test_raised_palm_stops_the_music_from_the_instinct():
    paused: list[str] = []

    def on_pause() -> dict:
        paused.append("pause")
        return {"ok": True}

    loop, _, clock, events = make(on_pause=on_pause)
    loop.home.apply("play_sound", {"sound": "song", "volume": 40})
    scene = SceneState(people=[PersonState(posture="standing", gesture="stop")])
    now = clock.now()
    loop.instincts.note(scene, now)
    guess = loop.instincts.guess(
        scene,
        loop.world.objects,
        loop.profile,
        now,
        music_on=True,
        spoke_recently=False,
        last_line="",
    )
    assert guess is not None and guess.kind == "stop_music"
    loop._perform_guess(guess, now)
    assert paused == ["pause"]
    assert any("stopped the music" in str(event.get("text", "")) for event in events)
