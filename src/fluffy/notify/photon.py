"""Send the fall text through Photon SMS.

A project key cannot text by itself. Photon needs the project id, the number
Photon sends from, a user for the caregiver, a space that holds both, then
the message. The space id is kept for the process so a later fall reuses it.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any

import httpx

log = logging.getLogger("fluffy.notify.photon")

API_BASE = "https://api.photon.codes"
ALERT_TO = "+14252686992"
ALERT_TEXT = "Edwin needs help! He fell down."

_shared: PhotonSms | None = None
_logged_skip = False


class PhotonError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"Photon returned {status}: {detail}")
        self.status = status


class PhotonSms:
    def __init__(
        self,
        api_key: str,
        project_id: str,
        from_number: str,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key.strip()
        self.project_id = project_id.strip()
        self.from_number = from_number.strip()
        self._client = client or httpx.Client()
        self._space_id: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.project_id and self.from_number)

    @classmethod
    def from_env(cls) -> PhotonSms:
        return cls(
            os.environ.get("PHOTON_API_KEY", ""),
            os.environ.get("PHOTON_PROJECT_ID", ""),
            os.environ.get("PHOTON_FROM", ""),
        )

    def send(self) -> None:
        """Text the caregiver. Raises PhotonError when Photon refuses the send."""
        sender = self._user(self.from_number)
        recipient = self._user(ALERT_TO)
        space = self._space(sender, recipient)
        self._post(
            "messages",
            {
                "senderId": sender,
                "spaceId": space,
                "content": {"type": "text", "format": "plain", "text": ALERT_TEXT},
            },
            idempotent=True,
        )

    def _user(self, handle: str) -> str:
        body = self._post("users", {"platform": "sms", "handle": handle})
        return str(body["id"])

    def _space(self, sender: str, recipient: str) -> str:
        if self._space_id:
            return self._space_id
        body = self._post(
            "spaces",
            {"platform": "sms", "participantIds": [sender, recipient]},
            idempotent=True,
        )
        self._space_id = str(body["id"])
        return self._space_id

    def _post(self, path: str, body: dict[str, Any], *, idempotent: bool = False) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if idempotent:
            headers["idempotency-key"] = str(uuid.uuid4())
        response = self._client.post(
            f"{API_BASE}/v1/projects/{self.project_id}/{path}",
            headers=headers,
            json=body,
            timeout=30,
        )
        if response.status_code not in (200, 201, 202):
            raise PhotonError(response.status_code, response.text.replace("\n", " ")[:300])
        data = response.json()
        if not isinstance(data, dict):
            raise PhotonError(response.status_code, "response was not an object")
        return data


def shared_sms() -> PhotonSms:
    global _shared
    if _shared is None:
        _shared = PhotonSms.from_env()
    return _shared


def plan_alerts(plan: Any) -> bool:
    if plan is None:
        return False
    return any(action.tool == "alert_caregiver" for action in plan.actions)


def notify_fall(sms: PhotonSms | None = None) -> bool:
    """Send the fixed help text. Missing setup or a Photon error does not raise."""
    global _logged_skip
    sender = sms or shared_sms()
    if not sender.configured:
        if not _logged_skip:
            log.info("photon sms skipped: set PHOTON_API_KEY, PHOTON_PROJECT_ID, and PHOTON_FROM")
            _logged_skip = True
        return False
    try:
        sender.send()
    except Exception as exc:
        log.warning("photon sms failed: %s", exc)
        return False
    return True


def notify_plan(plan: Any, sms: PhotonSms | None = None) -> bool:
    """Text only when this plan is the caregiver alert."""
    if not plan_alerts(plan):
        return False
    return notify_fall(sms)
