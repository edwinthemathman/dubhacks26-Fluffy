"""Cue policy. Vision leads; sound backs it up and breaks ties."""

from fluffy.brain.consent import ConsentManager
from fluffy.brain.cues import derive, judge, volume_request, wants_music_stop
from fluffy.clock import FrozenClock
from fluffy.ids import IdFactory
from fluffy.models import Action, Consent, Trigger, build_plan
from fluffy.perception.scene import PersonState, SceneState, SoundRead, apply_sound_read

from tests.inference.helpers import dim_plan, profiles


def _manager():
    clock = FrozenClock()
    return ConsentManager(clock), clock


def _scene(*people: PersonState, sounds: list[str] | None = None, lighting: str = "normal") -> SceneState:
    return SceneState(
        people=list(people),
        sounds=sounds or ["none"],  # type: ignore[arg-type]
        lighting=lighting,  # type: ignore[arg-type]
    )


def _asleep_person(**overrides) -> PersonState:
    fields = dict(posture="lying", activity="sleeping", eyes="closed", location_surface="sofa")
    fields.update(overrides)
    return PersonState(**fields)  # type: ignore[arg-type]


def _notify_plan(clock: FrozenClock, tool: str = "show_caption"):
    args = (
        {"text": "Someone is at the door", "size": "large", "priority": "normal"}
        if tool == "show_caption"
        else {"room": "living_room", "color": "amber", "pattern": "pulse3", "duration_s": 3}
    )
    return build_plan(
        clock=clock,
        ids=IdFactory(),
        trigger=Trigger(kind="inferred", room="living_room"),
        action_class="notify",
        intent="Someone is at the door",
        actions=[Action(action_id="act_1", tool=tool, args=args)],
        confidence=0.9,
        consent=Consent(required=False),
        needs=["caption"],
        delivered_via=["caption"],
        reasoning_summary="doorbell",
    )


def _play_plan(clock: FrozenClock):
    return build_plan(
        clock=clock,
        ids=IdFactory(),
        trigger=Trigger(kind="inferred", room="living_room"),
        action_class="comfort",
        intent="Play rain sounds",
        actions=[
            Action(
                action_id="act_1",
                tool="play_sound",
                args={"sound": "rain", "room": "living_room", "volume": 30},
            )
        ],
        confidence=0.9,
        consent=Consent(required=True),
        needs=["speech"],
        delivered_via=["speech"],
        reasoning_summary="rain",
    )


def test_snoring_dims_the_lights_without_a_question():
    manager, clock = _manager()
    scene = _scene(_asleep_person(activity="unknown", eyes="unknown"), sounds=["snoring"])
    update = manager.consider(
        dim_plan(clock, luminosity=30, confidence=0.9),
        profiles()["blind"],
        transcript_clear=False,
        scene=scene,
    )
    assert update.release
    assert update.plan is not None
    assert update.plan.consent.given_by == "cue"
    assert update.question is None


def test_quiet_visual_sleep_dims_without_a_question():
    manager, clock = _manager()
    scene = _scene(_asleep_person(), sounds=["none"])
    update = manager.consider(
        dim_plan(clock, luminosity=35),
        profiles()["speech_cognitive"],
        transcript_clear=False,
        scene=scene,
    )
    assert update.release
    assert update.plan is not None
    assert update.plan.consent.given_by == "cue"


def test_speech_keeps_a_lookalike_sleeper_awake():
    manager, clock = _manager()
    scene = _scene(_asleep_person(), sounds=["speech"])
    update = manager.consider(
        dim_plan(clock, luminosity=30),
        profiles()["blind"],
        transcript_clear=False,
        scene=scene,
    )
    assert update.status == "asking"
    assert update.release is False
    assert update.plan is not None
    assert update.plan.actions[0].tool == "dim_lights"
    assert "fall" not in derive(scene)


def test_arrival_turns_the_lights_on():
    manager, clock = _manager()
    scene = _scene(
        PersonState(posture="standing", activity="idle"),
        sounds=["door"],
    )
    update = manager.consider(
        dim_plan(clock, luminosity=80, intent="Turn the lights on"),
        profiles()["blind"],
        transcript_clear=False,
        scene=scene,
    )
    assert update.release
    assert update.plan is not None
    assert update.plan.consent.given_by == "cue"
    assert update.plan.actions[0].args["luminosity"] == 80


def test_walking_in_the_dark_without_sound_is_not_an_arrival():
    scene = _scene(
        PersonState(posture="walking", activity="idle"),
        sounds=["none"],
        lighting="dark",
    )
    assert "arriving" not in derive(scene)
    assert "getting_dark" in derive(scene)


def test_low_confidence_dim_asks():
    manager, clock = _manager()
    scene = _scene(_asleep_person(), sounds=["snoring"])
    update = manager.consider(
        dim_plan(clock, luminosity=30, confidence=0.69),
        profiles()["blind"],
        transcript_clear=False,
        scene=scene,
    )
    assert update.status == "asking"
    assert update.release is False
    assert update.question == "Should I dim the lights?"


def test_a_fall_only_asks_to_call_alex():
    manager, clock = _manager()
    scene = _scene(
        PersonState(posture="lying", on_floor=True, location_surface="floor", eyes="open"),
        sounds=["none"],
    )
    update = manager.consider(
        dim_plan(clock, luminosity=20),
        profiles()["blind"],
        transcript_clear=False,
        scene=scene,
    )
    assert update.status == "asking"
    assert update.release is False
    assert update.question == "Should I call Alex for help?"
    assert update.plan is not None
    assert [action.tool for action in update.plan.actions] == ["alert_caregiver"]


def test_help_sound_asks_even_when_they_look_asleep():
    scene = _scene(_asleep_person(), sounds=["help"])
    assert derive(scene) == {"fall"}
    assert judge(scene, None) == "ask_for_help"


def test_a_seen_fall_needs_no_words():
    scene = _scene(PersonState(fell=True, posture="sitting"), sounds=["none"])
    assert "fall" in derive(scene)
    assert judge(scene, None) == "ask_for_help"


def test_lying_on_an_unknown_surface_is_not_a_fall():
    scene = _scene(
        PersonState(posture="lying", location_surface="unknown", eyes="open"),
        sounds=["pain"],
    )
    assert "fall" not in derive(scene)


def test_a_thud_and_someone_lying_on_the_floor_is_a_fall():
    scene = _scene(
        PersonState(posture="lying", location_surface="floor"),
        sounds=["impact"],
    )
    assert derive(scene) >= {"fall"}
    assert "falling_asleep" not in derive(scene)


def test_holding_a_knee_and_groaning_while_standing_is_hurt():
    scene = _scene(
        PersonState(posture="standing", distress_signs=["holding_body"]),
        sounds=["pain"],
    )
    assert "fall" in derive(scene)


def test_a_grimace_without_pain_or_a_fall_is_not_an_emergency():
    scene = _scene(
        PersonState(posture="standing", distress_signs=["grimacing"]),
        sounds=["speech"],
    )
    assert "fall" not in derive(scene)


def test_resting_on_the_sofa_with_a_sigh_is_not_a_fall():
    scene = _scene(_asleep_person(), sounds=["none"])
    assert "fall" not in derive(scene)
    assert "falling_asleep" in derive(scene)


def test_doorbell_notifies():
    manager, clock = _manager()
    scene = _scene(sounds=["doorbell"])
    update = manager.consider(
        _notify_plan(clock),
        profiles()["deaf"],
        transcript_clear=False,
        scene=scene,
    )
    assert update.release
    assert update.plan is not None
    assert update.plan.consent.given_by == "profile"


def test_quiet_scene_produces_no_plan():
    scene = _scene(sounds=["none"])
    assert derive(scene) == set()
    assert judge(scene, None) == "quiet"


def test_play_sound_while_sleepy_asks():
    manager, clock = _manager()
    scene = _scene(_asleep_person(), sounds=["snoring"])
    update = manager.consider(
        _play_plan(clock),
        profiles()["blind"],
        transcript_clear=False,
        scene=scene,
    )
    assert update.status == "asking"
    assert update.release is False
    assert update.plan is not None
    assert update.plan.actions[0].tool == "play_sound"


def test_sound_read_replaces_the_scene_labels():
    scene = SceneState(sounds=["speech"], summary="someone on the sofa")
    merged = apply_sound_read(scene, SoundRead(sounds=["snoring"], heard="Soft snoring."))
    assert merged is not None
    assert merged.sounds == ["snoring"]
    assert merged.summary == "someone on the sofa"


def test_a_thud_after_someone_leaves_the_frame_is_not_enough():
    scene = _scene(PersonState(), sounds=["impact", "door"])
    assert "fall" not in derive(scene)


def test_a_thud_and_a_groan_with_nobody_in_view_is_not_enough():
    scene = _scene(sounds=["impact", "pain"])
    assert "fall" not in derive(scene)


def test_a_thud_while_someone_sits_fine_is_not_a_fall():
    scene = _scene(
        PersonState(posture="sitting", activity="idle", location_surface="sofa", eyes="open"),
        sounds=["impact"],
    )
    assert "fall" not in derive(scene)


def test_a_seen_or_heard_clap_is_clapping():
    assert "clapping" in derive(_scene(PersonState(gesture="clapping", posture="standing")))
    assert "clapping" in derive(_scene(sounds=["clap"]))
    assert "clapping" not in derive(SceneState(summary="they clap their hands"))


def test_a_raised_palm_or_the_word_stop_stops_the_music():
    assert "stop_music" in derive(_scene(PersonState(gesture="stop", posture="standing")))
    assert "stop_music" not in derive(SceneState(summary="they stop moving"))
    spoken = SceneState(said="stop the music")
    assert "stop_music" in derive(spoken)
    assert wants_music_stop("stop")
    assert not wants_music_stop("don't stop talking")


def test_spoken_volume_is_up_down_or_a_number():
    assert volume_request("turn it up") == {"op": "up"}
    assert volume_request("a bit quieter please") == {"op": "down"}
    assert volume_request("set the volume to 25") == {"op": "set", "level": 25}
    assert volume_request("mute") == {"op": "mute"}
    assert volume_request("turn the lights down") is None


def test_decision_text_includes_the_transcript():
    from fluffy.demo.analyze import _user_text, scene_prompt

    text = _user_text(
        profiles()["blind"],
        "",
        None,
        set(),
        transcript="I fell and I can't get up",
    )
    assert "Transcript: I fell and I can't get up" in text
    assert "When the words and the picture agree" in text
    laid_out = scene_prompt("where are my keys")
    assert "Transcript: where are my keys" in laid_out
    assert "objects_seen" in laid_out
