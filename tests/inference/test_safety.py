from fluffy.brain.safety import SafetyMachine
from fluffy.clock import FrozenClock

from tests.inference.helpers import profiles


def _tools(effects) -> list[str]:
    names = []
    for effect in effects:
        for plan in effect.plans:
            names.extend(action.tool for action in plan.actions)
    return names


def test_checkin_then_okay_watches_for_five_minutes():
    clock = FrozenClock()
    machine = SafetyMachine(clock, profiles()["blind"])
    effects = machine.on_event("possible_fall")
    assert effects[0].text == "Are you okay?"
    assert effects[0].speech is True
    assert effects[0].caption is True
    assert effects[0].light_pattern == "pulse1"
    assert "announce" in _tools(effects)
    assert "show_caption" in _tools(effects)

    cleared = machine.on_answer("YES")
    assert cleared[0].kind == "cleared"
    assert machine.phase == "watching"
    assert machine.on_event("possible_fall") == []

    clock.advance(600 - 1)
    assert machine.tick() == []
    assert machine.on_event("possible_fall") == []
    clock.advance(1)
    assert machine.tick() == []
    assert machine.phase == "idle"
    assert machine.on_event("possible_fall")[0].kind == "checkin"


def test_nod_cancels_and_shake_or_help_alerts_now():
    clock = FrozenClock()
    machine = SafetyMachine(clock, profiles()["blind"])
    machine.on_event("distress_call")
    cleared = machine.on_answer("nod")
    assert cleared[0].kind == "cleared"
    assert machine.phase == "watching"

    clock = FrozenClock()
    machine = SafetyMachine(clock, profiles()["speech_cognitive"])
    machine.on_event("possible_fall")
    effects = machine.on_answer("NEED HELP")
    assert effects[0].kind == "alert"
    assert effects[0].plans[0].actions[0].tool == "alert_caregiver"
    assert effects[0].plans[0].actions[0].args["severity"] == "high"
    assert "needs help" in effects[0].text
    assert effects[1].text == "Alex has been notified."
    assert machine.phase == "alerted"

    clock = FrozenClock()
    machine = SafetyMachine(clock, profiles()["blind"])
    machine.on_event("possible_fall")
    effects = machine.on_answer("shake")
    assert effects[0].kind == "alert"


def test_silence_repeats_then_alerts_with_the_fall_message():
    clock = FrozenClock()
    machine = SafetyMachine(clock, profiles()["blind"])
    machine.on_event("possible_fall")
    clock.advance(30)
    second = machine.tick()
    assert second[0].kind == "checkin"
    assert second[0].louder is True
    assert second[0].light_pattern == "strobe"
    assert machine.phase == "checkin_repeat"

    clock.advance(30)
    effects = machine.tick()
    assert effects[0].kind == "alert"
    assert effects[0].text == "Sam may have fallen in the living room and has not responded to check-ins."
    assert effects[0].plans[0].consent.given_by == "profile"
    assert effects[1].text == "Alex has been notified."

    clock.advance(60)
    followup = machine.tick()
    assert followup[0].kind == "checkin"
    assert followup[0].text == "Are you okay?"

    assert machine.acknowledge()[0].text == "Caregiver acknowledged."
    assert machine.phase == "idle"
    assert machine.tick() == []


def test_a_bare_no_asks_again():
    clock = FrozenClock()
    machine = SafetyMachine(clock, profiles()["blind"])
    machine.on_event("possible_fall")
    effects = machine.on_answer("no")
    assert effects[0].kind == "checkin"
    assert "need help" in effects[0].text.lower()
    assert machine.on_answer("No, I'm fine")[0].kind == "cleared"


def test_deaf_checkin_does_not_rely_on_speech():
    clock = FrozenClock()
    machine = SafetyMachine(clock, profiles()["deaf"])
    effects = machine.on_event("possible_fall")
    assert effects[0].speech is False
    assert effects[0].caption is True
    assert "announce" not in _tools(effects)
    assert "show_caption" in _tools(effects)
    assert "flash_lights" in _tools(effects)
    assert "speech" not in effects[0].plans[0].profile.delivered_via


def test_distress_after_alert_starts_another_checkin():
    clock = FrozenClock()
    machine = SafetyMachine(clock, profiles()["blind"])
    machine.on_event("possible_fall")
    clock.advance(30)
    machine.tick()
    clock.advance(30)
    assert machine.tick()[0].kind == "alert"
    assert machine.on_event("possible_fall") == []
    effects = machine.on_event("distress_call")
    assert effects[0].kind == "checkin"
    assert machine.phase == "checkin"


def test_okay_after_alert_stands_down():
    clock = FrozenClock()
    machine = SafetyMachine(clock, profiles()["blind"])
    machine.on_event("possible_fall")
    clock.advance(30)
    machine.tick()
    clock.advance(30)
    machine.tick()
    assert machine.phase == "alerted"
    cleared = machine.on_answer("I'm okay")
    assert cleared[0].kind == "cleared"
    assert cleared[0].text.startswith("Update:")
    assert machine.status == "resolved_ok"
    assert machine.on_event("possible_fall") == []
    clock.advance(600)
    machine.tick()
    assert machine.on_event("possible_fall")[0].kind == "checkin"


def test_stand_down_ends_the_incident():
    clock = FrozenClock()
    machine = SafetyMachine(clock, profiles()["blind"])
    machine.on_event("possible_fall")
    assert machine.stand_down()[0].kind == "cleared"
    assert machine.phase == "idle"
    assert machine.stand_down() == []


def test_distress_during_watch_starts_a_new_checkin():
    clock = FrozenClock()
    machine = SafetyMachine(clock, profiles()["blind"])
    machine.on_event("possible_fall")
    machine.on_answer("okay")
    assert machine.phase == "watching"
    effects = machine.on_event("distress_call")
    assert effects[0].kind == "checkin"
    assert machine.phase == "checkin"
