"""Typed and browser text enter the same v2 submission path as HTTP plans."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from fluffy.config import RuntimeSettings
from fluffy.ids import new_id
from fluffy.models.contracts import ActionProposal, Observation, PlanSubmission, SCHEMA_VERSION
from fluffy.profile import load_profile
from fluffy.server.runtime import Runtime, _error, _targets


@dataclass
class ProposedAction:
    tool: str
    args: dict
    reason: str = ""


@dataclass
class TextDecision:
    actions: list[ProposedAction] = field(default_factory=list)
    reply: str = ""
    error: str | None = None


class TextInterpreter(Protocol):
    async def interpret(self, text: str) -> TextDecision: ...


class UnavailableInterpreter:
    """No key is configured. Do not invent a device action."""

    async def interpret(self, text: str) -> TextDecision:
        del text
        return TextDecision(error="Perceptron is not configured. The words were not turned into a device action.")


class PerceptronTextInterpreter:
    def __init__(self, client, profile) -> None:
        self.client = client
        self.profile = profile

    async def interpret(self, text: str) -> TextDecision:
        from fluffy.live.perceive import decide_text

        result = await decide_text(self.client, profile=self.profile, utterance=text)
        if result.error:
            return TextDecision(error=result.error)
        actions = [
            ProposedAction(tool=call.name, args=dict(call.args), reason=call.reason)
            for call in result.device
            if call.name != "ask_user"
        ]
        return TextDecision(actions=actions, reply=result.reply or result.asked or "")


def build_text_interpreter(settings: RuntimeSettings) -> TextInterpreter:
    key = settings.integrations.perceptron_api_key
    if not key:
        return UnavailableInterpreter()
    from perceptron import AsyncClient

    profile = load_profile(settings.root / "config" / "profiles" / "speech_cognitive.toml")
    return PerceptronTextInterpreter(AsyncClient(api_key=key, provider="perceptron"), profile)


async def submit_text(
    runtime: Runtime,
    *,
    request_id: str,
    text: str,
    interpreter: TextInterpreter,
    explicit: bool,
) -> dict:
    """Interpret words, then submit. A returned status is the decision, not a fake effect."""

    if runtime.stopped:
        return _error("cancelled", "the session is stopped")
    if request_id in runtime.idempotent:
        return runtime.idempotent[request_id]
    decision = await interpreter.interpret(text)
    session_id = runtime.settings.session_id
    await runtime.emit(session_id, "turn.start", {"turn_id": request_id, "kind": "text" if explicit else "speech", "trigger": text[:80]})
    if decision.error:
        await runtime.emit(session_id, "think", {"turn_id": request_id, "note": decision.error[:180]})
        result = {"status": "unavailable", "request_id": request_id, "message": decision.error, "completed": False}
        runtime.idempotent[request_id] = result
        return result
    if not decision.actions:
        note = decision.reply or "No device action."
        await runtime.emit(session_id, "think", {"turn_id": request_id, "note": note[:180]})
        result = {"status": "no_action", "request_id": request_id, "message": note, "completed": False}
        runtime.idempotent[request_id] = result
        return result
    room = str(decision.actions[0].args.get("room") or runtime.settings.home.rooms[0].id)
    supports = ["explicit_request"] if explicit else ["spoken_request"]
    observation = Observation(
        observation_id=new_id("obs"),
        session_id=session_id,
        kind="speech",
        captured_at=runtime.clock.now(),
        room=room,
        supports=supports,
        targets=sorted({target for action in decision.actions for target in _targets(action.args)}),
        summary=text[:240],
        source="live",
    )
    runtime.remember_observation(observation)
    submission = PlanSubmission(
        schema_version=SCHEMA_VERSION,
        session_id=session_id,
        intent=text[:240],
        trigger={
            "kind": "speech",
            "event": "explicit_request" if explicit else "spoken_request",
            "room": room,
            "utterance": text[:240],
        },
        actions=[
            ActionProposal(
                action_id=new_id("act"),
                tool=action.tool,
                args=action.args,
                evidence_ids=[observation.observation_id],
            )
            for action in decision.actions[:8]
        ],
        reasoning_summary=(decision.actions[0].reason or decision.reply or "Interpreted request.")[:500],
    )
    result = await runtime.submit(submission, explicit=explicit)
    result["request_id"] = request_id
    result["completed"] = False
    runtime.idempotent[request_id] = result
    return result
