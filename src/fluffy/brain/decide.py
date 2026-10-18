"""One Perceptron decision call, plus up to two follow-ups for meta tools.

Device calls from the last round become a single plan. Calls from earlier
rounds are only context. Tool arguments that are not valid JSON, or that
fail the catalog, are dropped.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from fluffy.brain.cues import derive, squint_determined
from fluffy.clock import Clock
from fluffy.ids import IdFactory
from fluffy.models import Action, Consent, Plan, Trigger, build_plan
from fluffy.perception.scene import SceneState
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
    if profile.output.audio_only_forbidden:
        speech = (
            "Answer with captions and lights. Do not answer with sound alone. "
            "They may not hear a doorbell, an alarm, or someone calling, so you must."
        )
    elif profile.output.captions and profile.output.speech:
        speech = "Speak, and also caption."
    else:
        speech = "Speak. Describe space relative to the person."
    if profile.questions.confirm_back == "always":
        requests = "Spoken requests from this person get a confirm-back from the app, not from you."
    else:
        requests = "A clear spoken request from this person counts as their consent."
    return "\n".join(
        [
            "You are Fluffy, a home companion for someone with a disability.",
            "Your job is to notice what the person needs, often before they say it.",
            "",
            "HOW TO DECIDE",
            "1. Use the recent timestamped frames to establish what physically happened. "
            "Follow the movement across frames. The last frames show how things are now. "
            "A fall is a visible collapse to the floor that stays down. "
            "Sitting, bending, crouching, stretching, and bringing the hands together are not falls.",
            "2. Sound labels are uncertain support. A loud sound by itself does not establish a clap, "
            "an impact, or a fall. When the frames are clear and a sound label is ambiguous, follow the frames. "
            "Snoring can support sleep, and speech can support that someone is awake, only when the picture agrees.",
            "3. Combine the evidence. Visible hands striking together, with a sound at the same time, supports a clap. "
            "A visible collapse followed by remaining down supports a possible fall. "
            "A loud sound with unclear or missing video is an unidentified sound. "
            "Do not invent an event or propose an action from that ambiguity. "
            "Hearing the same sound described again is the same sound, not new evidence.",
            "4. Missing, stale, or blocked video means unknown, not that nothing happened. "
            "Do not drop a clear request because the camera cannot see it. "
            "If the frames clearly show the person upright and fine, do not describe a fall.",
            "5. The words say what they want. The picture says whether it applies and who said it. "
            "If they disagree on safety, trust the picture. "
            "A clear browser transcript can support a tool. The app still applies consent. "
            "An uncertain transcript, or speech from a television or another room, is not this person's intent. "
            "If it names an object, that object is what they are after. "
            "A request like 'help me get a bottle of water' is that request, not an emergency.",
            "6. In each tool reason, write one short evidence line: what the frames showed, "
            "what the audio supports, and what is still uncertain. Observable facts only. "
            "You cannot text a caregiver. The safety check-in does that on its own.",
            "",
            "WHEN TO CALL A TOOL",
            "- Do not call alert_caregiver. It is not one of your tools. "
            "A missed emergency and a false alarm both matter, and the safety check-in decides.",
            "- Dozing off, eyes closing, lying back to rest: dim_lights luminosity 10-30, "
            "and set_light_warmth warm.",
            "- A squint held across the frames, or a hand kept over the eyes: dim_lights "
            "luminosity 35 for the room and set_blinds position 0. Their eyes hurt. "
            "One blink, glasses glare, or bright eyes is not enough.",
            "- Room dark, or someone arriving in the dark: dim_lights luminosity 70-100.",
            "- Doorbell, knock, or alarm: flash_lights and show_caption.",
            "- When the words and the picture agree, call every tool they ask for, in the same turn: "
            "dim or turn a light on, warmth, blinds, a sound, or finding something they named.",
            "- One request can need several tools. Call all of them together. "
            "It's getting late and they want music: play_sound with sound=music and dim_lights "
            "in the same turn. Do not stop after the first part. "
            "Never text the caregiver yourself.",
            "- They ask to open or close the front or bedroom door: open_door or close_door.",
            "- They ask for a song or to stop the music: play_music or stop_music.",
            "- They ask you to text someone: send_text. The app always asks before it sends.",
            "- They ask you to call someone: call_person. That is a demo animation, not a real call.",
            "- If you can see something may be wanted but you're unsure what, call ask_user "
            "with one short, friendly question instead of guessing or doing nothing.",
            "- Nothing above, or nothing changed: call no tools and reply with an empty message.",
            "",
            "THE QUESTION YOU WRITE",
            "When you call comfort tools, write the question the person will hear as your reply, "
            "in the same turn as the tool calls. One short question. Name the action, and use "
            "the specific words they said.",
            'Good: "Should I put on some Spotify?"',
            'Good: "Should I dim the lights and put on music?"',
            "Bad: a list of what you noticed. These are wrong:",
            '"yawning, dozing off; listening to Spotify before sleep?"',
            '"person says it\'s getting late and asks for music?"',
            "ask_user uses this same shape. The reason field stays a few words about the cue. "
            "The reply is the question. If you call no tools, leave the reply empty.",
            "",
            "KEEPING THINGS CLOSE",
            "- Searching with no word said: call ask_user with choices taken from the tagged objects.",
            "- They name or choose an object: call find_object, then announce and show_caption "
            "saying where it is relative to them now.",
            "- Left behind or dropped: announce and show_caption. Add flash_lights for a Deaf profile.",
            "- Never describe an object the camera hasn't tagged or can't see.",
            "",
            "CONSENT IS HANDLED FOR YOU",
            "A tool call is only a proposal. The app asks the person before anything changes, "
            "Caregiver texts are not yours to send. "
            "So never hold back a fitting tool call to be polite or to wait for permission.",
            requests,
            "Habits are things the person asked for in advance. When one applies, propose its action "
            "with high confidence unless you can see a reason not to.",
            "",
            "EVERY DEVICE CALL",
            "confidence: 0.8-1.0 when the frames plainly show it, when the frames and the sound agree, "
            "when they clearly asked, or for a doorbell, knock, or alarm. "
            "0.3 or lower when you only heard it, or the video is unclear. "
            "Do not propose an action for a physical event at that level.",
            "reason: one short evidence line, e.g. 'frames show hands meeting; sound agrees; not a fall'.",
            "LOOK, THEN ACT",
            "If you need to look, find, or read before you act, call that tool first. "
            "You get the result back, and you can then call a device tool.",
            "Summarize what you noticed in the reason fields. Do not write a long chain of thought into a tool.",
            "Confidence below 0.7 is uncertain: the app asks and does not act. "
            "Confidence of 0.7 or more is certain enough for one plan.",
            "",
            "STYLE",
            "Describe space relative to the person. For atypical speech, guess generously. "
            'Never tell a blind person to "look", or a Deaf person to "listen". '
            "Don't narrate. Stay brief.",
            f"This person: {profile.display_name}. {speech}",
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
        if parsed.name == "alert_caregiver":
            rejected.append("alert_caregiver: blocked, only the safety check-in may text the caregiver")
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


def _asked_choices(meta: list[tuple[str, ParsedCall]]) -> list[str] | None:
    for _, call in meta:
        if call.name == "ask_user":
            raw = call.args.get("choices")
            if isinstance(raw, list) and raw:
                return [str(item) for item in raw]
            return None
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
    plan = build_plan(
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
    return complete_paired_request(plan, f"{reply} {reasons}")


def complete_paired_request(plan: Plan | None, text: str) -> Plan | None:
    """It's getting late and they want music: the plan keeps both actions.

    The question is built from the actions, so a one-tool plan would ask
    about music only. Add the missing half before that question is written.
    """
    if plan is None or any(action.tool == "alert_caregiver" for action in plan.actions):
        return plan
    if not _mentions_music(text) or not _mentions_dim(text):
        return plan
    actions = list(plan.actions)
    if not any(action.tool == "play_sound" for action in actions):
        actions.append(
            Action(
                action_id="act_music",
                tool="play_sound",
                args={"sound": "music", "room": "living_room", "volume": 40},
            )
        )
    if not any(action.tool == "dim_lights" for action in actions):
        actions.append(
            Action(
                action_id="act_dim",
                tool="dim_lights",
                args={"room": "living_room", "luminosity": 25, "light": "lamp.living_floor"},
            )
        )
    if len(actions) == len(plan.actions):
        return plan
    return plan.model_copy(update={"actions": actions, "action_class": "comfort"})


def ensure_eye_relief(
    plan: Plan | None,
    scene: SceneState | None,
    *,
    profile: Profile,
    clock: Clock,
    ids: IdFactory,
    held: bool = False,
) -> Plan | None:
    """A determined squint dims the lights and closes the blinds.

    The squint has to stay in view, or happen again in the same clip.
    A fall still belongs to the safety check-in, so this adds nothing then.
    """
    if not squint_determined(scene, held=held) or "fall" in derive(scene):
        return plan
    if plan is not None and any(action.tool == "alert_caregiver" for action in plan.actions):
        return plan
    actions = [action for action in (plan.actions if plan else []) if action.tool not in {"dim_lights", "set_blinds"}]
    actions.append(
        Action(
            action_id=ids("act"),
            tool="dim_lights",
            args={"room": "living_room", "luminosity": 35},
        )
    )
    actions.append(
        Action(
            action_id=ids("act"),
            tool="set_blinds",
            args={"room": "living_room", "position": 0},
        )
    )
    summary = "They are squinting, so the lights dim and the blinds close."
    if plan is None:
        return build_plan(
            clock=clock,
            ids=ids,
            trigger=Trigger(kind="inferred", event="squinting", room="living_room"),
            action_class="comfort",
            intent="Ease the light on their eyes",
            actions=actions,
            confidence=0.86,
            consent=Consent(required=True),
            needs=list(profile.needs),
            delivered_via=profile.delivery_channels(),
            reasoning_summary=summary,
        )
    confidence = plan.confidence if plan.confidence >= 0.7 else 0.86
    return plan.model_copy(
        update={
            "actions": actions,
            "action_class": "comfort",
            "confidence": confidence,
            "reasoning_summary": summary,
        }
    )


def _mentions_music(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in ("music", "a song", "some songs"))


def _mentions_dim(text: str) -> bool:
    lowered = text.lower()
    return any(
        phrase in lowered
        for phrase in (
            "getting late",
            "it's late",
            "it is late",
            "winding down",
            "going to sleep",
            "going to bed",
            "bedtime",
            "dim the light",
            "dim the lights",
            "too bright",
        )
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
