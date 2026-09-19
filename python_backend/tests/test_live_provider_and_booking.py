"""Provider parsing, live-slot normalization, booking sessions and email.

Nothing here contacts Oladoc: the page-shape logic is exercised against the
structures observed on the real site, so the suite stays fast and offline. The
live checks against oladoc.com are run separately.
"""

from __future__ import annotations

import pytest

from app import booking_session as bs
from app import email_service as es
from app import oladoc_provider as op


# --------------------------------------------------------------------------
# Query normalization
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("urologist", "urologist"),
    ("Urologist", "urologist"),
    ("  UROLOGIST  ", "urologist"),
    ("urology", "urologist"),
    ("Urology", "urologist"),
    ("heart specialist", "cardiologist"),
    ("cardiologist", "cardiologist"),
    ("cardiology", "cardiologist"),
    ("skin doctor", "dermatologist"),
    ("child specialist", "pediatrician"),
    ("ENT", "ent-specialist"),
    ("ear nose throat", "ent-specialist"),
    ("kidney specialist", "nephrologist"),
    ("general physician", "general-physician"),
])
def test_specialty_slug_handles_case_spacing_and_synonyms(text, expected):
    assert op.specialty_slug(text) == expected


def test_a_doctor_name_is_not_mistaken_for_a_specialty():
    assert op.specialty_slug("Dr Ahmed") == ""
    assert op.specialty_slug("Ahmed") == ""


def test_query_normalization_is_whitespace_and_punctuation_safe():
    assert op.normalize_query("  Dr.   Ahmed!! ") == "dr ahmed"


# --------------------------------------------------------------------------
# Provider URL parsing - this is what excludes labs
# --------------------------------------------------------------------------

def test_profile_url_yields_city_specialty_and_provider_id():
    parsed = op._parse_profile_href("/pakistan/lahore/dr/urologist/usama-nawaz-ghumman/3217663")
    assert parsed == {
        "city": "lahore", "specialty_slug": "urologist",
        "slug": "usama-nawaz-ghumman", "provider_doctor_id": "3217663",
    }


def test_lab_entries_are_recognised_as_non_doctors():
    lab = op._parse_profile_href("/pakistan/lahore/dr/radiology-lab/al-razi-healthcare-lab/221385")
    assert lab["specialty_slug"] in op.NON_DOCTOR_CATEGORIES
    pathology = op._parse_profile_href("/pakistan/karachi/dr/pathology-lab/one-health-lab/537141")
    assert pathology["specialty_slug"] in op.NON_DOCTOR_CATEGORIES


def test_booking_url_pins_clinic_and_doctor():
    match = op.BOOKING_URL_RE.search("/appointment/13580/3217663")
    assert match.group("clinic") == "13580"
    assert match.group("pid") == "3217663"


@pytest.mark.parametrize("label,expected", [
    ("08:30 PM", "20:30"), ("12:00 PM", "12:00"), ("12:30 AM", "00:30"),
    ("09:00 AM", "09:00"), ("11:45 PM", "23:45"),
])
def test_twelve_hour_times_convert_without_shifting(label, expected):
    assert op._to_24h(label) == expected


def test_listing_url_is_built_from_city_and_slug():
    assert op.listing_url("Lahore", "urologist") == "https://oladoc.com/pakistan/lahore/urologist"
    assert op.listing_url("  Karachi ", "cardiologist") == "https://oladoc.com/pakistan/karachi/cardiologist"


def test_slots_are_never_requested_for_another_doctor():
    doctor = {"doctor_id": "oladoc:111", "provider_doctor_id": "111"}
    with pytest.raises(op.ProviderError):
        # booking URL belongs to provider doctor 999, not 111
        op.get_live_slots(doctor, "2026-09-21", booking_url="https://oladoc.com/appointment/13580/999")


def test_invalid_date_is_rejected_before_any_browser_starts():
    with pytest.raises(ValueError):
        op.get_live_slots({"provider_doctor_id": "1"}, "not-a-date", clinic_id="2")


# --------------------------------------------------------------------------
# Booking session
# --------------------------------------------------------------------------

DOCTOR = {
    "doctor_id": "oladoc:2571726", "provider_doctor_id": "2571726",
    "doctor_name": "Dr. Ghulam Abbas", "specialty": "Urologist",
    "oladoc_profile_url": "https://oladoc.com/pakistan/lahore/dr/urologist/ghulam-abbas/2571726",
}


def _session(user="user-1"):
    return bs.registry.create(
        user_id=user, doctor=DOCTOR, date="2026-09-21",
        slot_id="2571726:17489:2026-09-21:15:30", slot_label="03:30 PM",
        clinic_id="17489", clinic_name="Akhtar Saeed Clinic",
    )


def test_session_pins_doctor_date_and_slot():
    session = _session()
    assert session["state"] == bs.CREATED
    assert session["doctorId"] == "oladoc:2571726"
    assert session["slotLabel"] == "03:30 PM"


def test_session_identity_cannot_be_changed_later():
    session = _session()
    for field, value in [("doctorId", "oladoc:999"), ("providerDoctorId", "999"),
                         ("date", "2026-10-01"), ("slotId", "other")]:
        with pytest.raises(bs.SessionError):
            bs.registry.transition(session["bookingSessionId"], bs.DOCTOR_VERIFIED, **{field: value})


def test_full_happy_path_transitions():
    session = _session()
    sid = session["bookingSessionId"]
    for state in [bs.DOCTOR_VERIFIED, bs.SLOT_SELECTED, bs.PATIENT_DETAILS_FILLED,
                  bs.OTP_REQUIRED, bs.OTP_VERIFIED, bs.BOOKING_SUBMITTED]:
        assert bs.registry.transition(sid, state)["state"] == state
    final = bs.registry.transition(sid, bs.CONFIRMED, providerAppointmentId="OLA-123")
    assert final["state"] == bs.CONFIRMED
    assert final["providerAppointmentId"] == "OLA-123"
    assert bs.summarize(final)["isConfirmed"] is True


def test_illegal_transitions_are_refused():
    session = _session()
    sid = session["bookingSessionId"]
    # Cannot jump straight to confirmed without submitting.
    with pytest.raises(bs.InvalidTransition):
        bs.registry.transition(sid, bs.CONFIRMED)
    bs.registry.transition(sid, bs.DOCTOR_VERIFIED)
    with pytest.raises(bs.InvalidTransition):
        bs.registry.transition(sid, bs.OTP_VERIFIED)


def test_confirmed_session_is_terminal():
    session = _session()
    sid = session["bookingSessionId"]
    for state in [bs.DOCTOR_VERIFIED, bs.SLOT_SELECTED, bs.PATIENT_DETAILS_FILLED,
                  bs.BOOKING_SUBMITTED, bs.CONFIRMED]:
        bs.registry.transition(sid, state)
    with pytest.raises(bs.InvalidTransition):
        bs.registry.transition(sid, bs.SLOT_SELECTED)


def test_a_session_belongs_to_one_account():
    session = _session(user="user-a")
    sid = session["bookingSessionId"]
    assert bs.registry.get(sid, user_id="user-b") is None
    assert bs.registry.get(sid, user_id="user-a") is not None
    with pytest.raises(bs.SessionError):
        bs.registry.transition(sid, bs.DOCTOR_VERIFIED, user_id="user-b")


def test_otp_state_is_exposed_but_no_otp_value_is_stored():
    session = _session()
    sid = session["bookingSessionId"]
    bs.registry.transition(sid, bs.DOCTOR_VERIFIED)
    bs.registry.transition(sid, bs.SLOT_SELECTED)
    updated = bs.registry.transition(sid, bs.PATIENT_DETAILS_FILLED)
    updated = bs.registry.transition(sid, bs.OTP_REQUIRED)
    summary = bs.summarize(updated)
    assert summary["awaitingOtp"] is True
    # No field anywhere holds an OTP value.
    assert not any("otp" in key.lower() and key != "awaitingOtp" for key in updated)


# --------------------------------------------------------------------------
# Confirmation email
# --------------------------------------------------------------------------

USER = {"fullName": "Ayesha Khan", "email": "patient@example.com"}
APPOINTMENT = {
    "id": "appt-1", "specialistName": "Dr. Ghulam Abbas", "specialistSpecialty": "Urologist",
    "clinicName": "Akhtar Saeed Clinic", "date": "2026-09-21", "timeLabel": "03:30 PM",
    "timezone": "Asia/Karachi", "providerAppointmentId": "OLA-77231", "providerName": "Oladoc",
}


def test_confirmation_email_contains_the_confirmed_details():
    message = es.build_confirmation_message(USER, APPOINTMENT)
    body = message.get_content()
    for token in ["Dr. Ghulam Abbas", "Urologist", "Akhtar Saeed Clinic",
                  "21 September 2026", "03:30 PM", "Ayesha Khan", "OLA-77231"]:
        assert token in body
    assert message["To"] == "patient@example.com"
    assert "confirmed" in message["Subject"].lower()


def test_email_is_not_sent_when_smtp_is_unconfigured(monkeypatch):
    for name in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    result = es.send_confirmation(USER, APPOINTMENT)
    assert result["status"] == es.EMAIL_NOT_CONFIGURED


def test_email_failure_is_reported_without_raising(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SMTP_PORT", "9")  # discard port: connection will fail
    monkeypatch.setenv("SMTP_USER", "u")
    monkeypatch.setenv("SMTP_PASSWORD", "p")
    result = es.send_confirmation(USER, APPOINTMENT)
    assert result["status"] == es.EMAIL_FAILED
    assert "appointment itself is unaffected" in result["detail"]


def test_missing_reference_is_simply_omitted():
    appointment = {**APPOINTMENT, "providerAppointmentId": "", "bookingReference": ""}
    body = es.build_confirmation_message(USER, appointment).get_content()
    assert "Reference:" not in body
