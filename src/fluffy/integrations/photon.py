"""Photon 2.0 send and delivery lookup.

Contract checked against https://api.photon.codes/openapi.json (October 2026):
POST /v1/projects/{id}/messages with idempotency-key, and
GET /v1/projects/{id}/messages/{messageId}.

The destination is a configured space id. The model cannot supply a number.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from fluffy.config import IntegrationsConfig

API_ROOT = "https://api.photon.codes"


class PhotonError(Exception):
    def __init__(self, error_code: str, message: str) -> None:
        self.error_code = error_code
        self.message = message
        super().__init__(message)


@dataclass
class PhotonSendResult:
    message_reference: str | None
    acceptance_status: str  # accepted, delivered, failed, unknown
    verification: str
    error_code: str | None = None
    detail: str = ""


class PhotonAdapter:
    def __init__(self, config: IntegrationsConfig, client: httpx.Client | None = None) -> None:
        self.config = config
        self._client = client or httpx.Client(timeout=8)

    def readiness(self) -> tuple[str, str]:
        if not self.config.photon_configured:
            return (
                "unavailable",
                "Photon project, API key, sender, and destination space are not all set",
            )
        return "configured", "Photon sender is configured. Delivery is confirmed only after the provider says so."

    def send_alert(self, incident_id: str, destination_ref: str, text: str, effect_key: str) -> PhotonSendResult:
        del incident_id  # eligibility is checked by the harness; the provider sees the text
        if not self.config.photon_configured:
            raise PhotonError("photon_not_configured", "Photon is not configured")
        if destination_ref != self.config.caregiver_destination:
            raise PhotonError("incident_not_eligible", "destination does not match the configured contact")
        url = f"{API_ROOT}/v1/projects/{self.config.photon_project_id}/messages"
        try:
            response = self._client.post(
                url,
                headers={
                    "Authorization": f"Bearer {self.config.photon_api_key}",
                    "idempotency-key": _idempotency(effect_key),
                    "content-type": "application/json",
                },
                json={
                    "content": {"type": "text", "format": "plain", "text": text},
                    "senderId": self.config.photon_sender_id,
                    "spaceId": destination_ref,
                },
            )
        except httpx.TimeoutException:
            return PhotonSendResult(None, "unknown", "unverified", "photon_timeout", "Photon acceptance timed out")
        except httpx.HTTPError:
            return PhotonSendResult(None, "unknown", "unverified", "provider_unknown", "Photon connection failed")
        if response.status_code == 202:
            body = response.json()
            reference = body.get("id")
            return _from_provider_status(reference, body.get("status"))
        if response.status_code >= 500:
            return PhotonSendResult(None, "unknown", "unverified", "provider_unknown", "Photon outcome is unknown")
        return PhotonSendResult(None, "failed", "unverified", "photon_rejected", f"Photon rejected the message ({response.status_code})")

    def get_delivery(self, message_reference: str) -> PhotonSendResult:
        if not self.config.photon_configured:
            raise PhotonError("photon_not_configured", "Photon is not configured")
        url = f"{API_ROOT}/v1/projects/{self.config.photon_project_id}/messages/{message_reference}"
        try:
            response = self._client.get(url, headers={"Authorization": f"Bearer {self.config.photon_api_key}"})
        except httpx.HTTPError:
            return PhotonSendResult(message_reference, "unknown", "unverified", "provider_unknown", "delivery lookup failed")
        if response.status_code != 200:
            return PhotonSendResult(message_reference, "unknown", "unverified", "provider_unknown", "delivery lookup was not confirmed")
        body = response.json()
        return _from_provider_status(message_reference, body.get("status"))


def _from_provider_status(reference: str | None, status: str | None) -> PhotonSendResult:
    if status == "delivered":
        return PhotonSendResult(reference, "delivered", "provider_delivered", detail="Delivered")
    if status in {"accepted", "dispatched"}:
        return PhotonSendResult(reference, "accepted", "provider_accepted", detail="Submitted")
    if status == "failed":
        return PhotonSendResult(reference, "failed", "unverified", "photon_rejected", "Photon reported failure")
    return PhotonSendResult(reference, "unknown", "unverified", "provider_unknown", "Photon status is unknown")


def _idempotency(effect_key: str) -> str:
    cleaned = "".join(ch for ch in effect_key if 33 <= ord(ch) <= 126)
    return (cleaned or "fluffy")[:255]
