"""Duplicate effects, authorization binding, and in-flight reconciliation."""

from datetime import datetime, timedelta, timezone

from fluffy.authz.store import action_hash
from fluffy.ledger.sqlite import EffectLedger
from fluffy.models.contracts import ActionResult, AuthorizationRecord, CheckOutcome, Plan, PlanAction, Trigger
from executor_stub.policy import authorize_plan

NOW = datetime(2026, 10, 18, 1, 0, tzinfo=timezone.utc)


def _plan(args=None) -> Plan:
    action = PlanAction(
        action_id="act_music_demo1",
        tool="play_sound",
        args=args or {"room": "living_room", "sound": "arrival_song", "volume": 35},
        evidence_ids=["obs_audio_17"],
        action_class="comfort",
    )
    return Plan(
        plan_id="pln_demo_0001",
        session_id="ses_demo",
        created_at=NOW,
        expires_at=NOW + timedelta(seconds=30),
        trigger=Trigger(kind="sound", event="clap", room="living_room"),
        intent="Play the arrival song",
        actions=[action],
        authorization_id="auth_demo_0001",
        reasoning_summary="A clap was heard and matched visible hand contact.",
    )


def _record(plan: Plan) -> AuthorizationRecord:
    return AuthorizationRecord(
        authorization_id=plan.authorization_id,
        session_id=plan.session_id,
        plan_id=plan.plan_id,
        action_hash=action_hash(plan.session_id, plan.actions),
        evidence_ids=["obs_audio_17"],
        checks=[CheckOutcome(name="cue_supported", passed=True, detail="fixture")],
        score=5,
        mode="automatic",
        action_classes={"act_music_demo1": "comfort"},
        expires_at=plan.expires_at,
        created_at=NOW,
    )


def test_changed_arguments_invalidate_authorization():
    plan = _plan()
    record = _record(plan)
    assert authorize_plan(plan, record, NOW, False) == ("approved", "authorized")
    tampered = plan.model_copy(deep=True)
    tampered.actions[0].args = {"room": "living_room", "sound": "arrival_song", "volume": 100}
    assert authorize_plan(tampered, record, NOW, False)[1] == "authorization_mismatch"


def test_cancelled_and_expired_plans_cannot_start():
    plan = _plan()
    record = _record(plan)
    assert authorize_plan(plan, record, NOW, True)[1] == "plan_cancelled"
    assert authorize_plan(plan, record, NOW + timedelta(seconds=31), False)[1] == "plan_expired"
    assert authorize_plan(plan, None, NOW, False)[1] == "authorization_missing"


def test_ledger_replays_and_rejects_payload_changes(tmp_path):
    ledger = EffectLedger(tmp_path / "ledger.sqlite")
    started, created = ledger.begin("ses_demo", "pln_demo_0001", "act_music_demo1", "hash-a", "spotify", NOW)
    assert created is True
    assert started.status == "started"
    result = ActionResult(
        action_id="act_music_demo1",
        plan_id="pln_demo_0001",
        session_id="ses_demo",
        tool="play_sound",
        status="succeeded",
        effect_kind="spotify",
        verification="playback_confirmed",
        started_at=NOW,
        finished_at=NOW,
        provider_reference="spotify:track:example",
    )
    ledger.finish(result, "hash-a")
    replay, created_again = ledger.begin("ses_demo", "pln_demo_0001", "act_music_demo1", "hash-a", "spotify", NOW)
    assert created_again is False
    assert replay.result is not None
    assert replay.result.status == "succeeded"
    try:
        ledger.begin("ses_demo", "pln_demo_0001", "act_music_demo1", "hash-b", "spotify", NOW)
        raise AssertionError("payload change should be rejected")
    except Exception as exc:
        assert exc.error_code == "duplicate_payload_mismatch"


def test_inflight_external_effect_becomes_unknown(tmp_path):
    ledger = EffectLedger(tmp_path / "ledger.sqlite")
    ledger.begin("ses_demo", "pln_demo_0001", "act_alert_demo1", "hash-a", "photon", NOW)
    unknown = ledger.reconcile_inflight(NOW + timedelta(seconds=5))
    assert unknown[0].status == "unknown"
    assert unknown[0].error_code == "provider_unknown"
    again = ledger.reconcile_inflight(NOW + timedelta(seconds=6))
    assert again == []
