"""Cal.com delivery: configuration, time conversion and channel dispatch.

These tests never reach the network. The one real end-to-end send against
Cal.com was performed manually during development and the booking cancelled
afterwards; what is pinned here is the logic around that call.
"""

from __future__ import annotations

import pytest

from app import calcom, email_service


# --------------------------------------------------------------- configuration

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("7099126", 7099126),
        ("  7099126  ", 7099126),
        ("", None),
        ("et_abcdef123456", None),   # the placeholder shipped in .env.example
        ("not-a-number", None),
    ],
)
def test_event_type_id_accepts_only_numeric_ids(monkeypatch, raw, expected):
    monkeypatch.setattr(calcom, "CAL_EVENT_TYPE_ID", raw)
    assert calcom.event_type_id() == expected


def test_placeholder_event_type_reports_unconfigured_rather_than_crashing(monkeypatch):
    """A placeholder must not blow up in int(); it must disable the channel."""
    monkeypatch.setattr(calcom, "CAL_API_KEY", "cal_live_example")
    monkeypatch.setattr(calcom, "CAL_EVENT_TYPE_ID", "et_abcdef123456")
    assert calcom.configured() is False
    assert calcom.calcom_status()["configured"] is False


def test_configured_requires_both_key_and_event_type(monkeypatch):
    monkeypatch.setattr(calcom, "CAL_EVENT_TYPE_ID", "7099126")
    monkeypatch.setattr(calcom, "CAL_API_KEY", "")
    assert calcom.configured() is False
    monkeypatch.setattr(calcom, "CAL_API_KEY", "cal_live_example")
    assert calcom.configured() is True


# ------------------------------------------------------------ time conversion

def test_local_clinic_time_converts_to_utc():
    # Pakistan is UTC+5 with no daylight saving.
    assert calcom.to_utc_iso("2026-09-21", "09:00", "Asia/Karachi") == "2026-09-21T04:00:00Z"


def test_twelve_hour_times_are_accepted():
    assert calcom.to_utc_iso("2026-09-21", "03:30 PM", "Asia/Karachi") == "2026-09-21T10:30:00Z"


def test_unparseable_time_is_reported_not_guessed():
    with pytest.raises(ValueError):
        calcom.to_utc_iso("2026-09-21", "sometime after lunch", "Asia/Karachi")


def test_unknown_timezone_falls_back_without_raising():
    """A missing tz database must not stop a confirmation going out."""
    assert calcom.to_utc_iso("2026-09-21", "09:00", "Not/AZone").endswith("Z")


# --------------------------------------------------------------- key redaction

def test_api_key_never_appears_in_an_error(monkeypatch):
    monkeypatch.setattr(calcom, "CAL_API_KEY", "cal_live_secret_value")
    assert "cal_live_secret_value" not in calcom._redact(
        "rejected for key cal_live_secret_value"
    )


# ------------------------------------------------------------ channel dispatch

@pytest.mark.parametrize(
    "value, expected",
    [("calcom", "calcom"), ("smtp", "smtp"), ("both", "both"),
     ("", "auto"), ("nonsense", "auto"), ("CALCOM", "calcom")],
)
def test_channel_selection(monkeypatch, value, expected):
    monkeypatch.setenv("CONFIRMATION_CHANNEL", value)
    assert email_service.confirmation_channel() == expected


def test_calcom_channel_is_used_when_forced(monkeypatch):
    monkeypatch.setenv("CONFIRMATION_CHANNEL", "calcom")
    calls = []

    def fake_notify(user, appointment):
        calls.append(appointment["id"])
        return {"status": "SENT", "detail": "ok", "attemptedAt": "now", "calcomBookingUid": "uid-1"}

    monkeypatch.setattr(calcom, "notify_appointment", fake_notify)
    monkeypatch.setattr(calcom, "configured", lambda: True)

    result = email_service.send_confirmation(
        {"email": "p@example.com", "fullName": "P"}, {"id": "a1"}
    )
    assert calls == ["a1"]
    assert result["status"] == email_service.EMAIL_SENT
    assert result["channel"] == "calcom"
    assert result["calcomBookingUid"] == "uid-1"


def test_auto_prefers_smtp_and_does_not_book_a_calcom_slot(monkeypatch):
    """Cal.com also books a slot, so auto must not use it when SMTP works."""
    monkeypatch.setenv("CONFIRMATION_CHANNEL", "auto")
    monkeypatch.setattr(email_service, "smtp_configured", lambda: True)
    monkeypatch.setattr(
        email_service, "_send_via_smtp",
        lambda u, a: {"status": email_service.EMAIL_SENT, "detail": "smtp", "channel": "smtp"},
    )

    def explode(user, appointment):  # pragma: no cover - must never run
        raise AssertionError("auto booked a Cal.com slot while SMTP was available")

    monkeypatch.setattr(calcom, "notify_appointment", explode)
    result = email_service.send_confirmation({"email": "p@example.com"}, {"id": "a1"})
    assert result["channel"] == "smtp"


def test_auto_falls_back_to_calcom_when_smtp_is_absent(monkeypatch):
    monkeypatch.setenv("CONFIRMATION_CHANNEL", "auto")
    monkeypatch.setattr(email_service, "smtp_configured", lambda: False)
    monkeypatch.setattr(calcom, "configured", lambda: True)
    monkeypatch.setattr(
        calcom, "notify_appointment",
        lambda u, a: {"status": "SENT", "detail": "cal", "attemptedAt": "now"},
    )
    result = email_service.send_confirmation({"email": "p@example.com"}, {"id": "a1"})
    assert result["channel"] == "calcom"


def test_no_channel_configured_is_reported_clearly(monkeypatch):
    monkeypatch.setenv("CONFIRMATION_CHANNEL", "auto")
    monkeypatch.setattr(email_service, "smtp_configured", lambda: False)
    monkeypatch.setattr(calcom, "configured", lambda: False)
    result = email_service.send_confirmation({"email": "p@example.com"}, {"id": "a1"})
    assert result["status"] == email_service.EMAIL_NOT_CONFIGURED


def test_missing_recipient_never_calls_a_provider(monkeypatch):
    def explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("attempted delivery with no recipient")

    monkeypatch.setattr(calcom, "notify_appointment", explode)
    result = email_service.send_confirmation({"email": ""}, {"id": "a1"})
    assert result["status"] == email_service.EMAIL_FAILED


def test_checklist_email_wins_over_the_account_email(monkeypatch):
    """The patient may give a different address in the checklist."""
    monkeypatch.setenv("CONFIRMATION_CHANNEL", "calcom")
    seen = {}

    def fake_notify(user, appointment):
        seen["to"] = appointment.get("patientEmail") or user.get("email")
        return {"status": "SENT", "detail": "ok", "attemptedAt": "now"}

    monkeypatch.setattr(calcom, "configured", lambda: True)
    monkeypatch.setattr(calcom, "notify_appointment", fake_notify)
    email_service.send_confirmation(
        {"email": "account@example.com"},
        {"id": "a1", "patientEmail": "checklist@example.com"},
    )
    assert seen["to"] == "checklist@example.com"
