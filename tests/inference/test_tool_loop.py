import asyncio
import json

from fluffy.brain.consent import ConsentManager
from fluffy.brain.decide import Decider
from fluffy.clock import FrozenClock
from fluffy.models import Trigger
from fluffy.models.tool_catalog import ToolError, parse_call
from fluffy.perception.provider import Completion, ScriptedProvider, ToolCall

from tests.inference.helpers import profiles


def _args(**fields) -> str:
    return json.dumps(fields)


def _dim(luminosity: int = 35) -> str:
    return _args(
        room="living_room",
        luminosity=luminosity,
        light="lamp.living_floor",
        confidence=0.9,
        reason="the person asked",
    )


def _run(provider: ScriptedProvider, *, reply_steps: list[Completion], trigger: Trigger):
    decider = Decider(
        provider,
        clock=FrozenClock(),
        meta_handler=lambda call: "The keys are on the table, to your left.",
    )
    provider.steps = list(reply_steps)
    profile = profiles()["blind"]
    return asyncio.run(
        decider.run(
            profile=profile,
            user_text="snapshot ctx_test\nwhere are my keys",
            trigger=trigger,
            snapshot_id="ctx_test",
        )
    )


def test_meta_results_are_resent_and_device_calls_become_one_plan():
    provider = ScriptedProvider([])
    result = _run(
        provider,
        reply_steps=[
            Completion(
                tool_calls=[
                    ToolCall("c1", "describe_scene", _args(detail="positions")),
                    ToolCall("c2", "dim_lights", _dim(80)),
                ]
            ),
            Completion(
                text="I'll dim the floor lamp.",
                tool_calls=[
                    ToolCall("c3", "dim_lights", _dim(35)),
                    ToolCall(
                        "c4",
                        "set_blinds",
                        _args(
                            room="living_room",
                            position=40,
                            confidence=0.8,
                            reason="less glare",
                        ),
                    ),
                ],
            ),
        ],
        trigger=Trigger(kind="spoken", utterance="dim that", room="living_room"),
    )
    assert result.rounds == 2
    assert result.plan is not None
    assert [action.tool for action in result.plan.actions] == ["dim_lights", "set_blinds"]
    assert result.plan.actions[0].args["luminosity"] == 35
    assert "confidence" not in result.plan.actions[0].args
    assert result.plan.confidence == 0.8
    assert result.plan.context_snapshot_id == "ctx_test"
    assert result.reply == "I'll dim the floor lamp."
    sent_again = " ".join(message.content for message in provider.seen[1])
    assert "on the table" in sent_again
    assert "where are my keys" in sent_again
    assert "The app asks the person before anything changes" in provider.seen[0][0].content


def test_tool_loop_stops_at_three_rounds():
    provider = ScriptedProvider([])
    steps = [
        Completion(tool_calls=[ToolCall(f"c{n}", "look", _args(question="where"))])
        for n in range(4)
    ]
    result = _run(provider, reply_steps=steps, trigger=Trigger(kind="inferred"))
    assert result.rounds == 3
    assert result.plan is None
    assert len(provider.steps) == 1


def test_malformed_arguments_are_rejected_and_valid_calls_still_group():
    provider = ScriptedProvider([])
    result = _run(
        provider,
        reply_steps=[
            Completion(
                text="Dim the floor lamp.",
                tool_calls=[
                    ToolCall("bad", "dim_lights", "{not json"),
                    ToolCall("worse", "dim_lights", _args(room="living_room", luminosity=300, confidence=0.9, reason="too far")),
                    ToolCall("ok", "dim_lights", _dim()),
                    ToolCall("nope", "make_coffee", "{}"),
                ],
            )
        ],
        trigger=Trigger(kind="spoken", utterance="dim it"),
    )
    assert result.plan is not None
    assert len(result.plan.actions) == 1
    assert result.plan.actions[0].args["luminosity"] == 35
    assert len(result.rejected) == 3


def test_spoken_plan_still_passes_through_consent():
    provider = ScriptedProvider([])
    result = _run(
        provider,
        reply_steps=[Completion(text="Dim the floor lamp", tool_calls=[ToolCall("ok", "dim_lights", _dim())])],
        trigger=Trigger(kind="spoken", utterance="dim the lamp"),
    )
    assert result.plan is not None
    spoken = ConsentManager(FrozenClock()).consider(
        result.plan, profiles()["blind"], transcript_clear=True
    )
    assert spoken.release
    assert spoken.plan is not None
    assert spoken.plan.consent.given_by == "spoken_request"

    inferred_plan = result.plan.model_copy(
        update={
            "trigger": Trigger(kind="inferred"),
            "consent": result.plan.consent.model_copy(update={"given_by": None}),
        }
    )
    held = ConsentManager(FrozenClock()).consider(
        inferred_plan, profiles()["blind"], transcript_clear=True
    )
    assert held.release is False
    assert held.status == "asking"


def test_catalog_rejects_malformed_json():
    try:
        parse_call("dim_lights", "{")
    except ToolError as exc:
        assert exc.detail == "malformed arguments"
    else:
        raise AssertionError("malformed JSON was accepted")
