"""Send a text through Photon iMessage.

A project key cannot text by itself. Photon needs the project id, then a
shared iMessage line for the recipient. The line is assigned by Photon, so
an SMS from-number is not required. The space id is kept so a later text
reuses it.
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
        self._spaces: dict[str, str] = {}

    @property
    def configured(self) -> bool:
        # A shared iMessage line is assigned by Photon. An SMS from-number is not required.
        return bool(self.api_key and self.project_id)

    @classmethod
    def from_env(cls) -> PhotonSms:
        return cls(
            os.environ.get("PHOTON_API_KEY", ""),
            os.environ.get("PHOTON_PROJECT_ID", ""),
            os.environ.get("PHOTON_FROM", ""),
        )

    def send(self, text: str | None = None, to: str | None = None) -> None:
        """Text one number over iMessage. Defaults keep the fall alert exactly as it is."""
        body = ALERT_TEXT if text is None else text
        handle = ALERT_TO if to is None else to
        sender = self._user(self._line_for(handle))
        recipient = self._user(handle)
        space = self._space(sender, recipient, handle)
        self._post(
            "messages",
            {
                "senderId": sender,
                "spaceId": space,
                "content": {"type": "text", "format": "plain", "text": body},
            },
            idempotent=True,
        )

    def _line_for(self, handle: str) -> str:
        """The iMessage line that talks to this person. A dedicated line can be set in PHOTON_FROM."""
        if self.from_number:
            return self.from_number
        page = self._get("platforms/imessage/assignments")
        for row in page.get("assignments") or []:
            if row.get("handle") == handle and not row.get("releasedAt"):
                return str(row["assignedPhoneNumber"])
        created = self._post("platforms/imessage/assignments", {"handle": handle})
        return str(created["assignedPhoneNumber"])

    def _user(self, handle: str) -> str:
        body = self._post("users", {"platform": "imessage", "handle": handle})
        return str(body["id"])

    def _space(self, sender: str, recipient: str, handle: str) -> str:
        cached = self._spaces.get(handle)
        if cached:
            return cached
        if handle == ALERT_TO and self._space_id:
            return self._space_id
        body = self._post(
            "spaces",
            {"platform": "imessage", "participantIds": [sender, recipient]},
            idempotent=True,
        )
        self._spaces[handle] = str(body["id"])
        if handle == ALERT_TO:
            self._space_id = self._spaces[handle]
        return self._spaces[handle]

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

    def _get(self, path: str) -> dict[str, Any]:
        response = self._client.get(
            f"{API_BASE}/v1/projects/{self.project_id}/{path}",
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=30,
        )
        if response.status_code != 200:
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


def notify_person(text: str, sms: PhotonSms | None = None) -> bool:
    """Text the person themselves. Quiet when PERSON_PHONE is unset."""
    number = os.environ.get("PERSON_PHONE", "").strip()
    sender = sms if sms is not None else shared_sms()
    if not number or not text.strip():
        return False
    if not sender.configured:
        return False
    try:
        sender.send(text=text, to=number)
    except Exception as exc:
        log.warning("person text failed: %s", exc)
        return False
    return True


def notify_fall(sms: PhotonSms | None = None) -> bool:
    """Send the fixed help text. Missing setup or a Photon error does not raise."""
    global _logged_skip
    sender = sms or shared_sms()
    if not sender.configured:
        if not _logged_skip:
            log.info("photon imessage skipped: set PHOTON_API_KEY and PHOTON_PROJECT_ID")
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
