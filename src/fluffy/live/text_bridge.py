"""Typed and browser text enter the same v2 submission path as HTTP plans."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Protocol

from datetime import timedelta

from fluffy.brain.arbiter import LOW_CONFIDENCE
from fluffy.config import RuntimeSettings
from fluffy.ids import new_id
from fluffy.models.contracts import ActionProposal, Observation, PlanAction, PlanSubmission, SCHEMA_VERSION
from fluffy.profile import load_profile
from fluffy.server.runtime import PendingQuestion, Runtime, _targets
from fluffy.tools.registry import ToolError, canonical_args

log = logging.getLogger("fluffy.text")


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
    thinking: str | None = None
    confidence: float | None = None
    asked: str | None = None
    choices: list[str] | None = None
    evidence: str = "transcript_only"


class TextInterpreter(Protocol):
    async def interpret(self, text: str, *, window=None) -> TextDecision: ...


class UnavailableInterpreter:
    """No key is configured. Do not invent a device action."""

    async def interpret(self, text: str, *, window=None) -> TextDecision:
        del text, window
        return TextDecision(error="Perceptron is not configured. The words were not turned into a device action.")


class PerceptronTextInterpreter:
    def __init__(self, client, profile) -> None:
        self.client = client
        self.profile = profile

    async def interpret(self, text: str, *, window=None) -> TextDecision:
        from fluffy.live.perceive import Window, decide, decide_text

        fresh = isinstance(window, Window) and len(window.frames) >= 2
        if fresh:
            result = await decide(
                self.client,
                window,
                profile=self.profile,
                utterance=text,
                scene=None,
                cues=set(),
                evidence_mode="video_plus_transcript",
            )
            evidence = "video_plus_transcript"
        else:
            result = await decide_text(self.client, profile=self.profile, utterance=text)
            evidence = "transcript_only"
        if result.error:
            return TextDecision(error=result.error, thinking=result.thinking, evidence=evidence)
        actions = [
            ProposedAction(tool=call.name, args=dict(call.args), reason=call.reason)
            for call in result.device
            if call.name != "ask_user"
        ]
        return TextDecision(
            actions=actions,
            reply=result.reply or "",
            thinking=result.thinking,
            confidence=result.confidence,
            asked=result.asked,
            choices=result.choices,
            evidence=evidence,
        )


def build_text_interpreter(settings: RuntimeSettings, client=None) -> TextInterpreter:
    key = settings.integrations.perceptron_api_key
    if not key:
        return UnavailableInterpreter()
    if client is None:
        from perceptron import AsyncClient

        client = AsyncClient(api_key=key, provider="perceptron")
    chosen = os.environ.get("FLUFFY_PROFILE", "elderly")
    path = settings.root / "config" / "profiles" / f"{chosen}.toml"
    if not path.exists():
        path = settings.root / "config" / "profiles" / "elderly.toml"
    profile = load_profile(path)
    return PerceptronTextInterpreter(client, profile)


def _public_failure(exc: BaseException) -> str:
    name = type(exc).__name__
    if name in {"ImportError", "ModuleNotFoundError"}:
        return "The text decision function is missing."
    if "timeout" in name.lower():
        return "Perceptron timed out while interpreting the request."
    return f"Interpretation failed ({name})."


async def submit_text(
    runtime: Runtime,
    *,
    request_id: str,
    text: str,
    interpreter: TextInterpreter,
    explicit: bool,
    window=None,
) -> dict:
    """Interpret words, then submit. A returned status is the decision, not a fake effect."""

    session_id = runtime.settings.session_id
    if request_id in runtime.idempotent:
        return runtime.idempotent[request_id]
    await runtime.emit(
        session_id,
        "turn.start",
        {"turn_id": request_id, "request_id": request_id, "kind": "text" if explicit else "speech", "trigger": text[:80]},
    )
    await runtime.emit(
        session_id,
        "inference.status",
        {"stage": "text", "status": "started", "request_id": request_id, "turn_id": request_id},
    )
    await runtime.emit(session_id, "think", {"turn_id": request_id, "note": "Interpreting request"})
    if runtime.stopped:
        return await _finish_text(runtime, request_id, "cancelled", "the session is stopped", "cancelled")
    try:
        decision = await interpreter.interpret(text, window=window)
    except Exception as exc:
        log.warning("text interpretation failed: %s", type(exc).__name__)
        return await _finish_text(runtime, request_id, "error", _public_failure(exc), "error")
    await _emit_thinking(runtime, request_id, decision.thinking)
    if decision.error:
        await runtime.emit(session_id, "think", {"turn_id": request_id, "note": decision.error[:180]})
        return await _finish_text(runtime, request_id, "unavailable", decision.error, "error")
    if not _certain(decision):
        return await _ask_instead(runtime, request_id, text, decision, explicit)
    if not decision.actions:
        note = decision.reply or "No device action."
        await runtime.emit(session_id, "think", {"turn_id": request_id, "note": note[:180]})
        return await _finish_text(runtime, request_id, "no_action", note, "nothing")
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
        reasoning_summary=_execution_line(decision),
    )
    evidence_ids = [observation.observation_id]
    if decision.evidence == "video_plus_transcript":
        video = Observation(
            observation_id=new_id("obs"),
            session_id=session_id,
            kind="video",
            captured_at=runtime.clock.now(),
            room=room,
            supports=["scene_change"],
            targets=observation.targets,
            summary="Fresh frames paired with the transcript."[:240],
            source="live",
        )
        runtime.remember_observation(video)
        evidence_ids.append(video.observation_id)
    submission = submission.model_copy(
        update={
            "actions": [
                action.model_copy(update={"evidence_ids": evidence_ids})
                for action in submission.actions
            ]
        }
    )
    await runtime.emit(
        session_id,
        "inference.status",
        {
            "stage": "execution",
            "status": "completed",
            "request_id": request_id,
            "turn_id": request_id,
            "thinking": _execution_line(decision)[:180],
        },
    )
    try:
        result = await runtime.submit(submission, explicit=explicit)
    except Exception as exc:
        log.warning("text submission failed: %s", type(exc).__name__)
        return await _finish_text(runtime, request_id, "error", _public_failure(exc), "error")
    result["request_id"] = request_id
    result["completed"] = False
    runtime.idempotent[request_id] = result
    await runtime.emit(
        session_id,
        "turn.end",
        {"turn_id": request_id, "request_id": request_id, "outcome": str(result.get("status") or "submitted")},
    )
    return result


def _certain(decision: TextDecision) -> bool:
    """0.7 is the line from the live arbiter: below it, ask and do not act."""

    if decision.asked or not decision.actions:
        return False
    if any(action.tool == "send_text" for action in decision.actions):
        return False
    if decision.confidence is None or decision.confidence < LOW_CONFIDENCE:
        return False
    return True


def _execution_line(decision: TextDecision) -> str:
    reasons = [action.reason.strip() for action in decision.actions if action.reason.strip()]
    if reasons:
        return "; ".join(reasons)[:500]
    reply = decision.reply.strip()
    if reply:
        return reply[:500]
    return "One home change."


def _question_for(decision: TextDecision) -> str:
    if decision.asked:
        return decision.asked.strip()
    reply = decision.reply.strip()
    if reply.endswith("?"):
        return reply
    if decision.actions:
        names = " and ".join(action.tool.replace("_", " ") for action in decision.actions[:3])
        return f"Should I {names}?"
    return reply


async def _ask_instead(runtime: Runtime, request_id: str, text: str, decision: TextDecision, explicit: bool) -> dict:
    question = _question_for(decision)
    if not decision.actions and not question:
        note = decision.reply or "No device action."
        await runtime.emit(runtime.settings.session_id, "think", {"turn_id": request_id, "note": note[:180]})
        return await _finish_text(runtime, request_id, "no_action", note, "nothing")
    if not question:
        question = "Should I do that?"
    session_id = runtime.settings.session_id
    room = str((decision.actions[0].args.get("room") if decision.actions else None) or runtime.settings.home.rooms[0].id)
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
    prepared: list[dict] = []
    for action in decision.actions[:8]:
        try:
            model = runtime.registry.validate_args(action.tool, action.args)
        except ToolError as exc:
            return await _finish_text(runtime, request_id, "error", exc.message, "error")
        prepared.append(
            PlanAction(
                action_id=new_id("act"),
                tool=action.tool,
                args=canonical_args(model),
                evidence_ids=[observation.observation_id],
                action_class=runtime.registry.get(action.tool).action_class,
            ).model_dump(mode="json")
        )
    question_id = new_id("qst")
    pending = PendingQuestion(
        question_id=question_id,
        session_id=session_id,
        text=question,
        choices=list(decision.choices or ["yes", "no"]),
        expires_at=runtime.clock.now() + timedelta(seconds=30),
        actions=prepared,
        trigger={
            "kind": "speech",
            "event": "explicit_request" if explicit else "spoken_request",
            "room": room,
            "utterance": text[:240],
        },
        intent=text[:240],
        reasoning_summary=_execution_line(decision) if decision.actions else question[:500],
    )
    runtime.questions[question_id] = pending
    await runtime.emit(session_id, "think", {"turn_id": request_id, "note": question[:180]})
    await runtime.emit(
        session_id,
        "question.opened",
        {"question_id": question_id, "text": question, "choices": pending.choices, "turn_id": request_id, "request_id": request_id},
    )
    result = {"status": "question", "question_id": question_id, "text": question, "request_id": request_id, "completed": False}
    runtime.idempotent[request_id] = result
    await runtime.emit(session_id, "turn.end", {"turn_id": request_id, "request_id": request_id, "outcome": "asked"})
    return result


async def _emit_thinking(runtime: Runtime, request_id: str, thinking: str | None) -> None:
    await runtime.emit(
        runtime.settings.session_id,
        "inference.status",
        {
            "stage": "text",
            "status": "completed",
            "request_id": request_id,
            "turn_id": request_id,
            "thinking": thinking or "",
        },
    )


async def _finish_text(runtime: Runtime, request_id: str, status: str, message: str, outcome: str) -> dict:
    session_id = runtime.settings.session_id
    await runtime.emit(
        session_id,
        "inference.error" if outcome in {"error", "cancelled"} else "think",
        {"turn_id": request_id, "request_id": request_id, "stage": "text", "message": message[:180], "note": message[:180]},
    )
    await runtime.emit(session_id, "turn.end", {"turn_id": request_id, "request_id": request_id, "outcome": outcome})
    result = {"status": status, "request_id": request_id, "message": message, "completed": False}
    runtime.idempotent[request_id] = result
    return result
