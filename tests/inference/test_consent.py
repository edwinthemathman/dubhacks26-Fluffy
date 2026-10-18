import pytest

from fluffy.brain.consent import ConsentManager
from fluffy.clock import FrozenClock

from tests.inference.helpers import dim_plan, profiles


def _manager(clock: FrozenClock | None = None) -> tuple[ConsentManager, FrozenClock]:
    clock = clock or FrozenClock()
    return ConsentManager(clock), clock


def test_inferred_comfort_is_asked_and_not_released():
    manager, clock = _manager()
    update = manager.consider(dim_plan(clock), profiles()["blind"], transcript_clear=True)
    assert update.release is False
    assert update.status == "asking"
    assert update.question == "Should I dim the lights?"
    assert update.plan is not None
    assert update.plan.consent.given_by is None


def test_clear_spoken_request_counts_as_consent_for_blind():
    manager, clock = _manager()
    update = manager.consider(
        dim_plan(clock, kind="spoken", confidence=0.91),
        profiles()["blind"],
        transcript_clear=True,
    )
    assert update.release
    assert update.plan is not None
    assert update.plan.consent.given_by == "spoken_request"


def test_low_confidence_and_unclear_speech_are_confirmed():
    manager, clock = _manager()
    blind = profiles()["blind"]
    low = manager.consider(
        dim_plan(clock, kind="spoken", confidence=0.4),
        blind,
        transcript_clear=True,
    )
    assert low.status == "asking"
    assert low.release is False

    manager, clock = _manager()
    unclear = manager.consider(
        dim_plan(clock, kind="spoken", confidence=0.95),
        blind,
        transcript_clear=False,
    )
    assert unclear.status == "asking"


def test_speech_cognitive_always_confirms():
    manager, clock = _manager()
    update = manager.consider(
        dim_plan(clock, kind="spoken", confidence=0.99),
        profiles()["speech_cognitive"],
        transcript_clear=True,
    )
    assert update.status == "asking"
    assert update.release is False


@pytest.mark.parametrize(
    ("method", "payload", "via"),
    [
        ("answer_voice", "yes", "voice"),
        ("answer_typing", "yes please", "typing"),
        ("answer_button", True, "button"),
        ("answer_nod", "nod", "nod"),
    ],
)
def test_each_answer_channel_approves(method: str, payload, via: str):
    manager, clock = _manager()
    manager.consider(dim_plan(clock), profiles()["blind"], transcript_clear=True)
    update = getattr(manager, method)(payload)
    assert update is not None
    assert update.release
    assert update.plan is not None
    assert update.plan.consent.given_by == via


def test_pointing_at_the_device_approves():
    manager, clock = _manager()
    manager.consider(dim_plan(clock), profiles()["blind"], transcript_clear=True)
    missed = manager.answer_pointing("light.living_ceiling")
    assert missed is not None
    assert missed.status == "ignored"
    assert manager.open is not None
    update = manager.answer_pointing("lamp.living_floor")
    assert update is not None
    assert update.release
    assert update.plan is not None
    assert update.plan.consent.given_by == "pointing"


def test_nod_none_and_unclear_speech_leave_the_question_open():
    manager, clock = _manager()
    manager.consider(dim_plan(clock), profiles()["blind"], transcript_clear=True)
    assert manager.answer_nod("none") is None
    unclear = manager.answer_voice("the other one")
    assert unclear is not None
    assert unclear.status == "ignored"
    assert manager.open is not None


def test_no_and_shake_decline():
    manager, clock = _manager()
    manager.consider(dim_plan(clock), profiles()["blind"], transcript_clear=True)
    update = manager.answer_voice("no")
    assert update is not None
    assert update.status == "declined"
    assert update.release is False

    manager, clock = _manager()
    manager.consider(dim_plan(clock), profiles()["blind"], transcript_clear=True)
    update = manager.answer_nod("shake")
    assert update is not None
    assert update.status == "declined"


def test_silence_is_no():
    manager, clock = _manager()
    manager.consider(dim_plan(clock), profiles()["blind"], transcript_clear=True)
    clock.advance(29)
    assert manager.tick() is None
    clock.advance(1)
    update = manager.tick()
    assert update is not None
    assert update.status == "declined"
    assert update.reason == "no answer"


def test_cognitive_profile_repeats_once_then_declines():
    manager, clock = _manager()
    asked = manager.consider(
        dim_plan(clock),
        profiles()["speech_cognitive"],
        transcript_clear=True,
    )
    assert asked.question == "Should I dim the lights?"
    clock.advance(20)
    repeated = manager.tick()
    assert repeated is not None
    assert repeated.status == "repeated"
    assert manager.open is not None
    clock.advance(20)
    update = manager.tick()
    assert update is not None
    assert update.status == "declined"


def test_low_confidence_inferred_offer_is_asked():
    manager, clock = _manager()
    update = manager.consider(
        dim_plan(clock, confidence=0.69),
        profiles()["blind"],
        transcript_clear=True,
    )
    assert update.status == "asking"
    assert update.release is False
    assert update.question == "Should I dim the lights?"
    assert manager.open is not None


def test_only_one_inferred_offer_every_two_minutes():
    manager, clock = _manager()
    blind = profiles()["blind"]
    first = manager.consider(dim_plan(clock, luminosity=30), blind, transcript_clear=True)
    assert first.status == "asking"
    manager.answer_button(True)
    second = manager.consider(dim_plan(clock, luminosity=10), blind, transcript_clear=True)
    assert second.status == "dropped"
    assert "2 minutes" in second.reason


def test_declined_offer_is_suppressed_for_30_minutes():
    manager, clock = _manager()
    blind = profiles()["blind"]
    manager.consider(dim_plan(clock), blind, transcript_clear=True)
    assert manager.answer_button(False).status == "declined"
    clock.advance(3 * 60)
    suppressed = manager.consider(dim_plan(clock), blind, transcript_clear=True)
    assert suppressed.status == "dropped"
    assert "declined" in suppressed.reason
    clock.advance(28 * 60)
    again = manager.consider(dim_plan(clock), blind, transcript_clear=True)
    assert again.status == "asking"


def test_cognitive_profile_asks_one_question_at_a_time():
    manager, clock = _manager()
    profile = profiles()["speech_cognitive"]
    first = manager.consider(dim_plan(clock, luminosity=40), profile, transcript_clear=True)
    assert first.status == "asking"
    second = manager.consider(dim_plan(clock, luminosity=20), profile, transcript_clear=True)
    assert second.status == "ignored"
    assert manager.open is not None
    assert manager.open.plan.actions[0].args["luminosity"] == 40
