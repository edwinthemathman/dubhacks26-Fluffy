"""Photon texts the caregiver only for a fall alert. HTTP is faked."""

import os

from fluffy.brain.cues import build_help_plan
from fluffy.clock import FrozenClock
from fluffy.notify.photon import ALERT_TEXT, ALERT_TO, PhotonSms, notify_plan
from tests.inference.helpers import dim_plan, profiles

CAREGIVER = "+15550002222"


class _Response:
    def __init__(self, status: int, payload: dict) -> None:
        self.status_code = status
        self._payload = payload
        self.text = ""

    def json(self) -> dict:
        return self._payload


class FakeHttp:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers or {}, "json": json})
        if url.endswith("/users"):
            user_id = "caregiver" if json["handle"] == CAREGIVER else "sender"
            return _Response(200, {"id": user_id})
        if url.endswith("/spaces"):
            return _Response(200, {"id": "space-1"})
        if url.endswith("/messages"):
            return _Response(202, {"id": "msg-1"})
        raise AssertionError(url)


def _sms(http: FakeHttp) -> PhotonSms:
    return PhotonSms(
        "test-key",
        "pho_prj_00000000000000000000000001",
        "+15550001111",
        client=http,
    )


def test_alert_caregiver_sends_the_plan_message(monkeypatch):
    monkeypatch.setenv("PHOTON_CAREGIVER_HANDLE", CAREGIVER)
    http = FakeHttp()
    sms = _sms(http)
    plan = build_help_plan(FrozenClock(), profiles()["blind"])

    assert notify_plan(plan, sms) is True

    users = [call["json"]["handle"] for call in http.calls if call["url"].endswith("/users")]
    assert CAREGIVER in users
    message = next(call for call in http.calls if call["url"].endswith("/messages"))
    assert message["json"]["content"] == {"type": "text", "format": "plain", "text": "Possible fall"}
    assert message["json"]["senderId"] == "sender"
    assert message["json"]["spaceId"] == "space-1"
    assert message["headers"]["Authorization"] == "Bearer test-key"
    assert message["headers"]["idempotency-key"]

    notify_plan(plan, sms)
    spaces = [call for call in http.calls if call["url"].endswith("/spaces")]
    assert len(spaces) == 1
    assert os.environ["PHOTON_CAREGIVER_HANDLE"] == CAREGIVER


def test_send_defaults_to_the_fall_text():
    http = FakeHttp()
    _sms(http).send()
    message = next(call for call in http.calls if call["url"].endswith("/messages"))
    assert message["json"]["content"]["text"] == ALERT_TEXT
    assert any(call["json"]["handle"] == ALERT_TO for call in http.calls if call["url"].endswith("/users"))


def test_notify_person_without_a_number_does_nothing(monkeypatch):
    from fluffy.notify.photon import notify_person

    monkeypatch.delenv("PERSON_PHONE", raising=False)
    http = FakeHttp()
    assert notify_person("You left your wallet on the side table.", _sms(http)) is False
    assert http.calls == []


def test_dim_lights_does_not_text():
    http = FakeHttp()
    sms = _sms(http)
    assert notify_plan(dim_plan(FrozenClock()), sms) is False
    assert http.calls == []
