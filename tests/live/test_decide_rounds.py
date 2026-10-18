"""A look-up call gets its answer, then a second call can act."""

import asyncio
import json
from types import SimpleNamespace

from fluffy.live.perceive import Window, decide

from tests.inference.helpers import profiles


class _Client:
    def __init__(self) -> None:
        self.sent: list[list] = []
        self.chat = self
        self.completions = self

    async def create(self, **kwargs):
        self.sent.append(kwargs["messages"])
        if len(self.sent) == 1:
            call = SimpleNamespace(id="c1", name="find_object", arguments='{"name":"glasses"}')
            return SimpleNamespace(text="", tool_calls=[call], reasoning=None, message={"role": "assistant"}, usage=None)
        call = SimpleNamespace(
            id="c2",
            name="announce",
            arguments=json.dumps(
                {
                    "text": "Your glasses are on the table.",
                    "room": "living_room",
                    "confidence": 0.9,
                    "reason": "found them",
                }
            ),
        )
        return SimpleNamespace(text="found", tool_calls=[call], reasoning=None, message={"role": "assistant"}, usage=None)


def test_a_find_is_answered_before_the_device_call():
    async def run():
        client = _Client()
        window = Window(frames=[(b"\xff\xd8jpeg", 0), (b"\xff\xd8jpeg", 1000)], seconds=1)
        result = await decide(
            client,
            window,
            profile=profiles()["elderly"],
            utterance="where are my glasses",
            scene=None,
            cues=set(),
        )
        assert result.rounds == 2
        assert [call.name for call in result.device] == ["announce"]
        assert any(item.get("role") == "tool" for item in client.sent[1])

    asyncio.run(run())
