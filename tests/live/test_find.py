"""Finding an object speaks a place, and does not wait on a full decision."""

from datetime import timedelta

from fluffy.models.tool_catalog import ParsedCall
from fluffy.perception.scene import ObjectSighting
from tests.live.test_live_loop import feed_frames, make, settle


def test_where_are_my_keys_uses_memory_and_skips_decide():
    async def run():
        loop, fake, clock, events = make()
        feed_frames(loop, clock)
        loop.world.objects.note_live(
            [ObjectSighting(name="keys", location_desc="on the counter", side="right")],
            clock.now(),
        )
        await loop.on_client({"type": "speech", "text": "where are my keys"})
        await settle(loop)
        assert fake.decide_calls == []
        said = [event.get("text", "") for event in events if event.get("type") == "say"]
        assert any("keys" in line.lower() and "counter" in line.lower() for line in said)

    import asyncio

    asyncio.run(run())


def test_a_miss_uses_the_last_place():
    async def run():
        loop, fake, clock, events = make()
        feed_frames(loop, clock)
        loop.world.objects.note_live(
            [ObjectSighting(name="glasses", location_desc="on the kitchen counter")],
            clock.now(),
        )
        clock.advance(timedelta(minutes=10).total_seconds())
        await loop.on_client({"type": "speech", "text": "where are my glasses"})
        await settle(loop)
        assert fake.decide_calls == []
        said = " ".join(event.get("text", "") for event in events if event.get("type") == "say")
        assert "don't see" in said.lower()
        assert "kitchen counter" in said.lower()

    import asyncio

    asyncio.run(run())


def test_find_object_from_the_model_is_spoken():
    async def run():
        loop, fake, clock, events = make()
        feed_frames(loop, clock)
        fake.meta = [
            ParsedCall(name="find_object", args={"name": "glasses"}, confidence=1, reason="asked", action_class="meta")
        ]

        async def locate(window, *, name, memory=None, now=None):
            from fluffy.live.perceive import Located

            del window, memory, now
            return Located(name, True, "Your glasses are on the left end of the side table, near the window.")

        fake.locate = locate
        await loop.on_client({"type": "speech", "text": "I dropped them"})
        await settle(loop)
        await asyncio.sleep(0.5)
        await settle(loop)
        said = [event.get("text", "") for event in events if event.get("type") == "say"]
        assert any("side table" in line for line in said)

    import asyncio

    asyncio.run(run())
