"""Frozen-clock coverage for the two check-in windows."""

from datetime import datetime, timedelta, timezone

from fluffy.brain.safety import IncidentManager, classify_reply
from fluffy.config import load_home_config, repo_root
from fluffy.models.contracts import PossibleFallEvent

NOW = datetime(2026, 10, 18, 1, 0, tzinfo=timezone.utc)


def _manager():
    home = load_home_config(repo_root() / "config" / "home.toml", {})
    return IncidentManager(home)


def _fall(seconds_on_floor=3, source="fixture"):
    return PossibleFallEvent(
        event_id="evt_fall_0001",
        session_id="ses_demo",
        room="living_room",
        descent_at=NOW - timedelta(seconds=seconds_on_floor),
        floor_at=NOW,
        source=source,
    )


def _finish_speech(manager, commands, now):
    spoken = next(command for command in commands if command.kind == "announce")
    manager.on_playback_ended(spoken.message_id, now)
    return spoken


def test_not_okay_is_not_okay():
    assert classify_reply("I'm okay") == "okay"
    assert classify_reply("I'm not okay") == "ambiguous"
    assert classify_reply("I am not okay") == "ambiguous"
    assert classify_reply("help me") == "help"
    assert classify_reply("that was helpful") == "ambiguous"


def test_short_floor_gap_does_not_open():
    manager = _manager()
    assert manager.open_possible_fall(_fall(seconds_on_floor=1), NOW) == []
    assert manager.incident is None


def test_both_windows_then_one_alert():
    manager = _manager()
    first = manager.open_possible_fall(_fall(), NOW)
    assert [command.kind for command in first] == ["stop_sound", "announce"]
    assert "I'm okay" in first[1].text
    _finish_speech(manager, first, NOW)
    assert manager.poll(NOW + timedelta(seconds=11)) == []
    second = manager.poll(NOW + timedelta(seconds=12))
    assert second[0].kind == "announce"
    assert "trusted contact" in second[0].text
    _finish_speech(manager, second, NOW + timedelta(seconds=12))
    assert manager.poll(NOW + timedelta(seconds=23)) == []
    alerts = manager.poll(NOW + timedelta(seconds=24))
    assert len(alerts) == 1
    assert alerts[0].kind == "alert"
    assert manager.claim_send(alerts[0].incident_id) is True
    assert manager.claim_send(alerts[0].incident_id) is False
    assert manager.poll(NOW + timedelta(seconds=40)) == []


def test_okay_during_either_window_cancels_the_send():
    manager = _manager()
    first = manager.open_possible_fall(_fall(), NOW)
    spoken = _finish_speech(manager, first, NOW)
    assert manager.answer(spoken.question_id, "I'm okay", None, NOW + timedelta(seconds=3))[0].kind == "resolved"
    assert manager.poll(NOW + timedelta(seconds=30)) == []
    assert manager.claim_send(manager.incident.incident_id) is False

    manager = _manager()
    first = manager.open_possible_fall(_fall(), NOW)
    _finish_speech(manager, first, NOW)
    second = manager.poll(NOW + timedelta(seconds=12))
    spoken = _finish_speech(manager, second, NOW + timedelta(seconds=12))
    assert manager.answer(spoken.question_id, None, "im_okay", NOW + timedelta(seconds=14))[0].kind == "resolved"
    assert manager.poll(NOW + timedelta(seconds=40)) == []


def test_help_sends_immediately_and_old_question_cannot_resolve_a_new_one():
    manager = _manager()
    first = manager.open_possible_fall(_fall(), NOW)
    old_question = first[1].question_id
    _finish_speech(manager, first, NOW)
    alerts = manager.answer(old_question, None, "need_help", NOW + timedelta(seconds=2))
    assert alerts[0].kind == "alert"
    assert manager.claim_send(alerts[0].incident_id) is True

    manager = _manager()
    first = manager.open_possible_fall(_fall(), NOW)
    stale = first[1].question_id
    _finish_speech(manager, first, NOW)
    second = manager.poll(NOW + timedelta(seconds=12))
    assert manager.answer(stale, "I'm okay", None, NOW + timedelta(seconds=13)) == []
    assert manager.incident.phase == "speaking_2"


def test_ambiguous_reply_does_not_cancel():
    manager = _manager()
    first = manager.open_possible_fall(_fall(), NOW)
    spoken = _finish_speech(manager, first, NOW)
    assert manager.answer(spoken.question_id, "I'm not okay", None, NOW + timedelta(seconds=1)) == []
    assert manager.incident.phase == "window_1"


def test_audio_failure_starts_the_window_and_is_explained():
    manager = _manager()
    first = manager.open_possible_fall(_fall(), NOW)
    manager.on_audio_unavailable(first[1].message_id, NOW)
    second = manager.poll(NOW + timedelta(seconds=12))
    manager.on_audio_unavailable(second[0].message_id, NOW + timedelta(seconds=12))
    alerts = manager.poll(NOW + timedelta(seconds=24))
    assert "audio check-in" in alerts[0].text


def test_pause_cancels_unsent_escalation():
    manager = _manager()
    manager.open_possible_fall(_fall(), NOW)
    assert "stopped" in manager.pause_sensing()
    assert manager.poll(NOW + timedelta(seconds=60)) == []
    assert manager.claim_send(manager.incident.incident_id) is False


def test_duplicate_fall_does_not_open_a_second_incident():
    manager = _manager()
    first = manager.open_possible_fall(_fall(), NOW)
    assert manager.open_possible_fall(_fall(), NOW + timedelta(seconds=1)) == []
    assert first[0].incident_id == manager.incident.incident_id
