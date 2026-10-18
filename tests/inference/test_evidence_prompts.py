"""Scene and decision prompts follow the frames, and do not confirm a bang."""

from fluffy.brain.decide import system_prompt
from fluffy.demo.analyze import SOUND_PROMPT, _user_text, scene_prompt

from tests.inference.helpers import profiles


def test_decision_prompt_keeps_frames_ahead_of_sound():
    prompt = system_prompt(profiles()["blind"])
    assert "timestamped frames" in prompt
    assert "Sitting, bending, crouching" in prompt
    assert "A loud sound by itself does not establish" in prompt
    assert "unidentified sound" in prompt
    assert "Missing, stale, or blocked video means unknown" in prompt
    assert "television" in prompt
    assert "what the frames showed" in prompt
    assert "Do not call alert_caregiver" in prompt
    assert "thud backs up a fall" not in prompt


def test_scene_and_sound_prompts_do_not_invent_a_fall():
    scene = scene_prompt("I need help")
    assert "visible collapse" in scene
    assert "bringing the hands together are not falls" in scene
    assert "Words do not set fell" in scene
    assert "A thud or crash right after" not in scene
    assert "A loud sound by itself is unidentified" in SOUND_PROMPT
    assert "same sound, not a second event" in SOUND_PROMPT
    assert "use impact" not in SOUND_PROMPT
    assert "television" in SOUND_PROMPT


def test_decision_context_covers_the_representative_cases():
    text = _user_text(profiles()["blind"], "help", None, set(), transcript="I fell, I can't get up")
    assert "unidentified sound, not a fall" in text
    assert "same sound described again is not new evidence" in text
    assert "camera cannot see them" in text
    assert "Do not call alert_caregiver" in text
    assert "thud and the person is out of view" not in text
