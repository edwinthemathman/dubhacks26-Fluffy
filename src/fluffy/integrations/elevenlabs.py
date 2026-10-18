"""ElevenLabs speech synthesis.

HTTP success means audio was generated. It does not mean anyone heard it.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from fluffy.config import IntegrationsConfig


class SpeechError(Exception):
    def __init__(self, error_code: str, message: str) -> None:
        self.error_code = error_code
        self.message = message
        super().__init__(message)


@dataclass
class SpeechClip:
    audio: bytes
    content_type: str = "audio/mpeg"


class ElevenLabsAdapter:
    def __init__(self, config: IntegrationsConfig, client: httpx.Client | None = None) -> None:
        self.config = config
        self._client = client or httpx.Client(timeout=8)

    def readiness_detail(self) -> tuple[str, str]:
        if not self.config.elevenlabs_configured:
            return "unavailable", "ElevenLabs voice, model, and API key are not all set"
        return "configured", "Credentials are set. Readiness becomes ready after a synthesis succeeds."

    def synthesize(self, text: str, timeout_s: float) -> SpeechClip:
        if not self.config.elevenlabs_configured:
            raise SpeechError("elevenlabs_not_configured", "ElevenLabs is not configured")
        voice = self.config.elevenlabs_voice_id
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice}/stream"
        try:
            response = self._client.post(
                url,
                headers={
                    "xi-api-key": self.config.elevenlabs_api_key,
                    "accept": "audio/mpeg",
                    "content-type": "application/json",
                },
                json={"text": text, "model_id": self.config.elevenlabs_model_id},
                timeout=timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise SpeechError("synthesis_failed", "speech synthesis timed out") from exc
        except httpx.HTTPError as exc:
            raise SpeechError("synthesis_failed", "speech synthesis connection failed") from exc
        if response.status_code >= 400:
            raise SpeechError("synthesis_failed", f"speech synthesis failed ({response.status_code})")
        if not response.content:
            raise SpeechError("synthesis_failed", "speech synthesis returned empty audio")
        return SpeechClip(audio=response.content, content_type=response.headers.get("content-type", "audio/mpeg"))
