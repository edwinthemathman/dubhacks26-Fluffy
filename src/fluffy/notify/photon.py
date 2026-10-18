"""Send texts through Photon 2.0.

A project key cannot text by itself. Photon needs the project id, the number
Photon sends from, a user for each phone, a space that holds both, then the
message. Spaces are kept per recipient so a later text to the same person
reuses the space. Numbers and handles live in the environment, never here.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any

import httpx

log = logging.getLogger("fluffy.notify.photon")

API_BASE = "https://api.photon.codes"
DEFAULT_HELP = "Someone needs help in the living room."
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
        """Text one number. Defaults keep the fall alert on the demo number."""
        self.send_text(ALERT_TO if to is None else to, ALERT_TEXT if text is None else text)

    def send_text(self, to: str, text: str) -> None:
        """Text `to`. Raises PhotonError when Photon refuses the send."""
        sender = self._user(self.from_number or self._line_for(to))
        recipient = self._user(to)
        space = self._space(sender, recipient, to)
        self._post(
            "messages",
            {
                "senderId": sender,
                "spaceId": space,
                "content": {"type": "text", "format": "plain", "text": text},
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
        body = self._post(
            "spaces",
            {"platform": "imessage", "participantIds": [sender, recipient]},
            idempotent=True,
        )
        self._spaces[handle] = str(body["id"])
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
    if not number or not text.strip() or not sender.configured:
        return False
    try:
        sender.send_text(number, text)
    except Exception as exc:
        log.warning("person text failed: %s", exc)
        return False
    return True


def notify_fall(
    message: str | None = None,
    sms: PhotonSms | None = None,
    to: str | None = None,
) -> bool:
    """Text the caregiver. Missing setup or a Photon error does not raise."""
    global _logged_skip
    sender = sms or shared_sms()
    handle = (to or os.environ.get("PHOTON_CAREGIVER_HANDLE") or ALERT_TO).strip()
    body = message or (ALERT_TEXT if not to and not os.environ.get("PHOTON_CAREGIVER_HANDLE") else DEFAULT_HELP)
    if not sender.configured or not handle:
        if not _logged_skip:
            log.info(
                "photon sms skipped: set PHOTON_API_KEY, PHOTON_PROJECT_ID, PHOTON_FROM, and PHOTON_CAREGIVER_HANDLE"
            )
            _logged_skip = True
        return False
    try:
        sender.send_text(handle, body if message is None else message)
    except Exception as exc:
        log.warning("photon sms failed: %s", exc)
        return False
    return True


def notify_plan(plan: Any, sms: PhotonSms | None = None) -> bool:
    """Text only when this plan is the caregiver alert."""
    if not plan_alerts(plan):
        return False
    message = None
    for action in plan.actions:
        if action.tool == "alert_caregiver":
            message = action.args.get("message")
            break
    return notify_fall(message if isinstance(message, str) else None, sms)
