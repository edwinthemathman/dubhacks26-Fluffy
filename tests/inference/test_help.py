"""A check-in starts from evidence, not from one weak hint."""

from fluffy.brain.help import HelpLadder, HelpSignal
from fluffy.brain.safety_legacy import SafetyMachine
from fluffy.clock import FrozenClock

from tests.inference.helpers import profiles


def test_one_impact_does_not_check_in():
    ladder = HelpLadder()
    assert ladder.observe(HelpSignal("impact", "supporting", "sound", 0)) == "none"


def test_ah_is_not_a_signal():
    ladder = HelpLadder()
    assert ladder.signals == []


def test_one_floor_read_waits():
    ladder = HelpLadder()
    assert ladder.note_floor(0) == "none"


def test_two_floor_reads_then_a_yes_checks_in():
    ladder = HelpLadder()
    assert ladder.note_floor(0) == "none"
    assert ladder.note_floor(6) == "confirm"
    assert ladder.confirm(on_floor="yes", hurt="unclear", okay="unclear") == "checkin"


def test_pain_plus_seeing_distress_checks_in():
    ladder = HelpLadder()
    assert ladder.observe(HelpSignal("pain", "supporting", "sound", 1)) == "none"
    assert ladder.observe(HelpSignal("distress", "supporting", "vision", 2)) == "checkin"


def test_spoken_help_while_speaking_is_immediate():
    ladder = HelpLadder()
    assert ladder.observe(HelpSignal("help", "strong", "words", 0)) == "checkin"


def test_help_from_the_television_does_nothing():
    ladder = HelpLadder()
    assert ladder.observe(HelpSignal("help", "weak", "sound", 0)) == "none"


def test_the_model_alone_does_not_check_in():
    ladder = HelpLadder()
    assert ladder.observe(HelpSignal("alert_caregiver", "supporting", "model", 0)) == "none"
    assert ladder.observe(HelpSignal("pain", "supporting", "sound", 1)) == "checkin"


def test_no_i_am_fine_is_okay_and_a_bare_no_asks_again():
    clock = FrozenClock()
    machine = SafetyMachine(clock, profiles()["blind"])
    machine.on_event("possible_fall")
    again = machine.on_answer("no")
    assert again[0].kind == "checkin"
    fine = machine.on_answer("No, I'm fine")
    assert fine[0].kind == "cleared"


def test_no_answer_alerts_only_while_the_evidence_holds():
    clock = FrozenClock()
    strong = SafetyMachine(clock, profiles()["blind"])
    strong.on_event("possible_fall", strength="strong")
    clock.advance(30)
    assert strong.tick()[0].kind == "checkin"
    clock.advance(31)
    assert strong.tick()[0].kind == "alert"

    clock2 = FrozenClock()
    soft = SafetyMachine(clock2, profiles()["blind"])
    soft.on_event("possible_fall", strength="supporting")
    soft.evidence_present = False
    clock2.advance(30)
    assert soft.tick()[0].kind == "checkin"
    clock2.advance(31)
    cleared = soft.tick()
    assert cleared[0].kind == "cleared"
    assert soft.phase == "idle"


def test_two_upright_reads_stand_down():
    ladder = HelpLadder()
    assert ladder.note_upright() is False
    assert ladder.note_upright() is True
