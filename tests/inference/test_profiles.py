from fluffy.brain.consent import ConsentManager
from fluffy.clock import FrozenClock

from tests.inference.helpers import dim_plan, profiles


def test_deaf_profile_is_never_audio_only():
    deaf = profiles()["deaf"]
    channels = deaf.delivery_channels()
    assert deaf.output.audio_only_forbidden is True
    assert deaf.output.speech is False
    assert "speech" not in channels
    assert "caption" in channels
    assert "light" in channels
    assert "speech" not in deaf.delivery_channels(safety=True)


def test_profile_presets_match_the_plan():
    loaded = profiles()
    assert set(loaded) == {"blind", "deaf", "speech_cognitive"}

    blind = loaded["blind"]
    assert blind.output.speech is True
    assert blind.output.speech_rate == 1.0
    assert blind.questions.confirm_back == "low_confidence"
    assert blind.input.voice is True
    assert "keys" in blind.vocabulary

    cognitive = loaded["speech_cognitive"]
    assert cognitive.output.speech_rate == 0.85
    assert cognitive.output.captions is True
    assert cognitive.questions.confirm_back == "always"
    assert cognitive.questions.style == "one_at_a_time"
    assert cognitive.questions.repeat_once is True
    assert cognitive.questions.repeat_after_s == 20
    assert cognitive.input.nod is True
    assert cognitive.input.pointing is True


def test_notify_plan_is_preagreed_for_every_profile():
    from fluffy.ids import IdFactory
    from fluffy.models import Action, Consent, Trigger, build_plan

    clock = FrozenClock()
    plan = build_plan(
        clock=clock,
        ids=IdFactory(),
        trigger=Trigger(kind="sound", event="doorbell"),
        action_class="notify",
        intent="Someone is at the door",
        actions=[
            Action(
                action_id="act_1",
                tool="show_caption",
                args={"text": "Doorbell.", "size": "large", "priority": "normal"},
            )
        ],
        confidence=0.95,
        consent=Consent(required=False),
        needs=["captions"],
        delivered_via=["caption"],
        reasoning_summary="doorbell",
    )
    for profile in profiles().values():
        update = ConsentManager(clock).consider(plan, profile, transcript_clear=True)
        assert update.release
        assert update.plan is not None
        assert update.plan.consent.given_by == "profile"
