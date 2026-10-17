"""One Perceptron decision call, plus up to two follow-ups for meta tools.

Device calls from the last round become a single plan. Calls from earlier
rounds are only context. Tool arguments that are not valid JSON, or that
fail the catalog, are dropped.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from fluffy.clock import Clock
from fluffy.ids import IdFactory
from fluffy.models import Action, Consent, Plan, Trigger, build_plan
from fluffy.models.tool_catalog import (
    DEVICE_TOOLS,
    META_TOOLS,
    ParsedCall,
    ToolError,
    action_class,
    parse_call,
    perceptron_tools,
)
from fluffy.perception.provider import Completion, DecisionProvider, Message, ToolCall
from fluffy.profile.model import Profile

MAX_ROUNDS = 3

MetaHandler = Callable[[ParsedCall], str]


@dataclass
class TurnResult:
    reply: str
    plan: Plan | None
    rejected: list[str] = field(default_factory=list)
    rounds: int = 0
    confirmations: list[tuple[str, str]] = field(default_factory=list)
    asked: str | None = None


class Decider:
    def __init__(
        self,
        provider: DecisionProvider,
        *,
        clock: Clock,
        ids: IdFactory | None = None,
        meta_handler: MetaHandler | None = None,
    ) -> None:
        self.provider = provider
        self.clock = clock
        self.ids = ids or IdFactory()
        self.meta_handler = meta_handler or _default_meta
        self.tools = perceptron_tools()

    async def run(
        self,
        *,
        profile: Profile,
        user_text: str,
        trigger: Trigger,
        snapshot_id: str,
    ) -> TurnResult:
        messages = [
            Message(role="system", content=system_prompt(profile)),
            Message(role="user", content=user_text),
        ]
        rejected: list[str] = []
        confirmations: list[tuple[str, str]] = []
        asked: str | None = None

        for round_index in range(MAX_ROUNDS):
            completion = await self.provider.complete(messages, self.tools)
            meta, device, bad = _split(completion.tool_calls)
            rejected.extend(bad)
            confirmations.extend(_confirmations(meta))
            asked = _asked(meta) or asked
            last_round = round_index == MAX_ROUNDS - 1 or not meta
            if last_round:
                return TurnResult(
                    reply=completion.text or asked or "",
                    plan=_plan_from_devices(
                        device,
                        profile=profile,
                        trigger=trigger,
                        snapshot_id=snapshot_id,
                        reply=completion.text,
                        clock=self.clock,
                        ids=self.ids,
                    ),
                    rejected=rejected,
                    rounds=round_index + 1,
                    confirmations=confirmations,
                    asked=asked,
                )
            messages = [
                *messages,
                Message(role="assistant", content=_assistant_trace(completion)),
                *[
                    Message(
                        role="tool",
                        content=self.meta_handler(call),
                        tool_call_id=call_id,
                    )
                    for call_id, call in meta
                ],
            ]

        raise RuntimeError("tool loop ended without a result")


def system_prompt(profile: Profile) -> str:
    words = ", ".join(profile.vocabulary) or "none"
    speech = "Speak, and also caption." if profile.output.captions and profile.output.speech else (
        "Use captions and lights. Do not rely on sound."
        if profile.output.audio_only_forbidden
        else "Speak. Describe space relative to the person."
    )
    confirm = (
        "Always confirm before changing anything."
        if profile.questions.confirm_back == "always"
        else "A clear spoken request counts as consent. Confirm when you are unsure."
    )
    return "\n".join(
        [
            "You are Fluffy, a home companion.",
            "Never change the home without consent.",
            "Describe space relative to the person.",
            "For atypical speech, guess generously, then confirm.",
            'Never tell a blind person to "look", or a Deaf person to "listen".',
            "Don't narrate. Stay brief.",
            "If nothing is needed, call no tools and reply with an empty message.",
            f"This person: {profile.display_name}. {speech}",
            confirm,
            f"Words they use: {words}.",
            f"Speech rate: {profile.output.speech_rate}.",
        ]
    )


def _split(
    calls: list[ToolCall],
) -> tuple[list[tuple[str, ParsedCall]], list[ParsedCall], list[str]]:
    meta: list[tuple[str, ParsedCall]] = []
    device: list[ParsedCall] = []
    rejected: list[str] = []
    for call in calls:
        try:
            parsed = parse_call(call.name, call.arguments)
        except ToolError as exc:
            rejected.append(f"{exc.name}: {exc.detail}")
            continue
        if parsed.name in META_TOOLS:
            meta.append((call.id, parsed))
        elif parsed.name in DEVICE_TOOLS:
            device.append(parsed)
        else:
            rejected.append(f"{parsed.name}: unknown tool")
    return meta, device, rejected


def _confirmations(meta: list[tuple[str, ParsedCall]]) -> list[tuple[str, str]]:
    found = []
    for _, call in meta:
        if call.name == "confirm_plan":
            found.append((str(call.args["plan_id"]), str(call.args["answer"])))
    return found


def _asked(meta: list[tuple[str, ParsedCall]]) -> str | None:
    for _, call in meta:
        if call.name == "ask_user":
            return str(call.args["question"])
    return None


def _plan_from_devices(
    device: list[ParsedCall],
    *,
    profile: Profile,
    trigger: Trigger,
    snapshot_id: str,
    reply: str,
    clock: Clock,
    ids: IdFactory,
) -> Plan | None:
    if not device:
        return None
    classes = [action_class(call.name) for call in device]
    chosen = _strictest(classes)
    actions = [
        Action(action_id=ids("act"), tool=call.name, args=call.args) for call in device
    ]
    confidence = min(call.confidence for call in device)
    reasons = "; ".join(call.reason for call in device if call.reason)
    intent = reply.strip() or reasons or "Proposed home change"
    return build_plan(
        clock=clock,
        ids=ids,
        trigger=trigger,
        action_class=chosen,  # type: ignore[arg-type]
        intent=intent,
        actions=actions,
        confidence=confidence,
        consent=Consent(required=chosen == "comfort"),
        needs=list(profile.needs),
        delivered_via=profile.delivery_channels(),
        reasoning_summary=reasons or intent,
        evidence=[],
        snapshot_id=snapshot_id,
    )


def _strictest(classes: list[str]) -> str:
    if "comfort" in classes:
        return "comfort"
    if "safety" in classes:
        return "safety"
    return "notify"


def _assistant_trace(completion: Completion) -> str:
    payload: dict[str, Any] = {
        "text": completion.text,
        "tool_calls": [
            {"id": call.id, "name": call.name, "arguments": call.arguments}
            for call in completion.tool_calls
        ],
    }
    return json.dumps(payload)


def _default_meta(call: ParsedCall) -> str:
    return f"{call.name} is not available in this turn"
