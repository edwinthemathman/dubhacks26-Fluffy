"""ElevenLabs Flash text-to-speech. A missing key or a failed call means unavailable.

The browser then speaks with its built-in voice. Fixed safety lines are generated
once at startup so a check-in never waits on the network.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import AsyncIterator

import httpx

DEFAULT_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"
DEFAULT_MODEL = "eleven_flash_v2_5"
SAFETY_LINES = (
    "Are you okay?",
    "Alex has been notified.",
    "Okay. I'll keep an eye on the room.",
)


class ElevenLabsVoice:
    def __init__(
        self,
        api_key: str,
        voice_id: str = DEFAULT_VOICE_ID,
        model_id: str = DEFAULT_MODEL,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key.strip()
        self.voice_id = voice_id.strip() or DEFAULT_VOICE_ID
        self.model_id = model_id.strip() or DEFAULT_MODEL
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None
        self.available = bool(self.api_key)
        self._cache: OrderedDict[tuple[str, str, float, str], bytes] = OrderedDict()
        self._limit = 64

    @classmethod
    def from_env(cls) -> ElevenLabsVoice:
        from fluffy.config import load_integrations

        config = load_integrations()
        return cls(
            api_key=config.elevenlabs_api_key,
            voice_id=config.elevenlabs_voice_id or DEFAULT_VOICE_ID,
            model_id=config.elevenlabs_model_id or DEFAULT_MODEL,
        )

    def _key(self, text: str, speed: float) -> tuple[str, str, float, str]:
        return (self.voice_id, self.model_id, round(float(speed), 2), text)

    def cached(self, text: str, speed: float) -> bytes | None:
        key = self._key(text, speed)
        found = self._cache.get(key)
        if found is not None:
            self._cache.move_to_end(key)
        return found

    async def stream(self, text: str, speed: float) -> AsyncIterator[bytes]:
        """MP3 chunks. Yields nothing when the service is down."""
        hit = self.cached(text, speed)
        if hit is not None:
            yield hit
            return
        if not self.available or not text.strip():
            return
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}/stream"
        body = {
            "text": text,
            "model_id": self.model_id,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
                "speed": float(speed),
            },
        }
        headers = {"xi-api-key": self.api_key}
        chunks: list[bytes] = []
        try:
            async with self._client.stream(
                "POST",
                url,
                params={"output_format": "mp3_44100_128"},
                json=body,
                headers=headers,
                timeout=10,
            ) as response:
                if response.status_code >= 400:
                    self.available = False
                    return
                async for chunk in response.aiter_bytes():
                    if chunk:
                        chunks.append(chunk)
                        yield chunk
        except (httpx.TimeoutException, httpx.HTTPError):
            self.available = False
            return
        if chunks:
            self._store(text, speed, b"".join(chunks))

    async def audio(self, text: str, speed: float) -> bytes | None:
        parts: list[bytes] = []
        async for chunk in self.stream(text, speed):
            parts.append(chunk)
        if not parts:
            return None
        return b"".join(parts)

    async def pregenerate(self, lines: tuple[str, ...] | list[str], speeds: tuple[float, ...]) -> None:
        if not self.available:
            return
        for text in lines:
            for speed in speeds:
                await self.audio(text, speed)

    def _store(self, text: str, speed: float, data: bytes) -> None:
        key = self._key(text, speed)
        self._cache[key] = data
        self._cache.move_to_end(key)
        while len(self._cache) > self._limit:
            self._cache.popitem(last=False)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
