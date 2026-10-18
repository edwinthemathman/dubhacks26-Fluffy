"""The decision-call interface. Tests use a scripted provider.

A live Perceptron client belongs behind this same interface. A request that
uses tools cannot also force a JSON schema, so scene calls and decision
calls stay separate.
"""

from dataclasses import dataclass, field
from typing import Literal, Protocol


@dataclass
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None = None


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class Completion:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"


class DecisionProvider(Protocol):
    async def complete(
        self, messages: list[Message], tools: list[dict]
    ) -> Completion: ...


class ScriptedProvider:
    """Replays a fixed list of completions and records each request."""

    def __init__(self, steps: list[Completion]) -> None:
        self.steps = list(steps)
        self.seen: list[list[Message]] = []

    async def complete(self, messages: list[Message], tools: list[dict]) -> Completion:
        self.seen.append(list(messages))
        if not self.steps:
            raise AssertionError("decision provider was called with no scripted reply left")
        return self.steps.pop(0)
