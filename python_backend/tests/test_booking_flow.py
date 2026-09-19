"""The booking flow end to end, driven by a stub provider.

A stub driver stands in for Oladoc so the whole state machine - including the
OTP hand-off, provider confirmation, the appointment record and the confirmation
email - runs without contacting a real provider or creating a real appointment.
"""

from __future__ import annotations

import time

import pytest

from app import appointments
from app import booking_session as bs
from app import booking_worker as bw
from app import email_service as es
from app.db import db

DOCTOR = {
    "doctor_id": "oladoc:2571726", "provider_doctor_id": "2571726",
    "doctor_name": "Dr. Ghulam Abbas", "specialty": "Urologist",
    "provider": "oladoc",
    "oladoc_profile_url": "https://oladoc.com/pakistan/lahore/dr/urologist/ghulam-abbas/2571726",
}
CLINIC = {"clinicId": "17489", "name": "Akhtar Saeed Clinic", "fee": "Rs. 2,500",
          "address": "Johar Town, Lahore"}
PATIENT = {"id": "u-1", "fullName": "Ayesha Khan", "email": "patient@example.com", "phone": "+923001234567"}


class StubDriver:
    """Provider stand-in. Records what it was asked to do."""

    def __init__(self, *, needs_otp=True, confirm=True, mismatch=False, slot_gone=False):
        self.needs_otp, self.confirm = needs_otp, confirm
        self.mismatch, self.slot_gone = mismatch, slot_gone
        self.opened_for = None
        self.selected = None
        self.filled = None
        self.otp_seen = None
        self.submitted = False
        self.closed = False

    def open_and_verify(self, doctor):
        if self.mismatch:
            raise bw.DoctorMismatch("Provider showed Dr. Someone Else")
        self.opened_for = doctor["doctor_id"]
        return {"doctor_name": doctor["doctor_name"], "provider_doctor_id": doctor["provider_doctor_id"]}

    def select_slot(self, date, time_label):
        if self.slot_gone:
            return False
        self.selected = (date, time_label)
        return True

    def fill_patient(self, patient):
        self.filled = dict(patient)
        return {"name": True, "email": True, "phone": True}

    def otp_required(self):
        return self.needs_otp

    def submit_otp(self, code):
        self.otp_seen = code
        return bool(code)

    def submit_booking(self):
        self.submitted = True
        return {"confirmed": self.confirm, "providerAppointmentId": "OLA-55120" if self.confirm else ""}

    def close(self):
        self.closed = True


def _start(driver, user_id="u-1"):
    session = bs.registry.create(
        user_id=user_id, doctor=DOCTOR, date="2026-09-21",
        slot_id="2571726:17489:2026-09-21:15:30", slot_label="03:30 PM",
        clinic_id=CLINIC["clinicId"], clinic_name=CLINIC["name"],
    )
    confirmed_with = {}
    bw.start_booking(
        session=session, doctor=DOCTOR, patient=PATIENT, booking_url="https://example.test/appointment",
        driver=driver, on_confirmed=lambda s: confirmed_with.update(s),
    )
    return session, confirmed_with


def _wait_for(session_id, predicate, timeout=6.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        session = bs.registry.get(session_id)
        if session and predicate(session):
            return session
        time.sleep(0.05)
    return bs.registry.get(session_id)


# --------------------------------------------------------------------------
# OTP hand-off
# --------------------------------------------------------------------------

def test_flow_pauses_at_otp_and_resumes_when_the_patient_supplies_it(monkeypatch):
    monkeypatch.setenv("OLADOC_SUBMIT_MODE", "live")
    driver = StubDriver(needs_otp=True, confirm=True)
    session, confirmed_with = _start(driver)
    sid = session["bookingSessionId"]

    paused = _wait_for(sid, lambda s: s["state"] == bs.OTP_REQUIRED)
    assert paused["state"] == bs.OTP_REQUIRED
    assert bs.summarize(paused)["awaitingOtp"] is True
    assert driver.submitted is False, "must not submit before the patient verifies"

    assert bw.provide_otp(sid, "445566") is True
    done = _wait_for(sid, lambda s: s["state"] in {bs.CONFIRMED, bs.FAILED})

    assert done["state"] == bs.CONFIRMED
    assert driver.otp_seen == "445566"
    assert driver.submitted is True
    assert done["providerAppointmentId"] == "OLA-55120"
    assert confirmed_with.get("state") == bs.CONFIRMED


def test_otp_value_is_never_stored_on_the_session(monkeypatch):
    monkeypatch.setenv("OLADOC_SUBMIT_MODE", "live")
    driver = StubDriver(needs_otp=True)
    session, _ = _start(driver)
    sid = session["bookingSessionId"]
    _wait_for(sid, lambda s: s["state"] == bs.OTP_REQUIRED)
    bw.provide_otp(sid, "998877")
    done = _wait_for(sid, lambda s: s["state"] in {bs.CONFIRMED, bs.FAILED})

    flattened = repr(done)
    assert "998877" not in flattened, "the OTP must not be retained on the session"


def test_flow_skips_otp_when_the_provider_does_not_ask(monkeypatch):
    monkeypatch.setenv("OLADOC_SUBMIT_MODE", "live")
    driver = StubDriver(needs_otp=False, confirm=True)
    session, _ = _start(driver)
    done = _wait_for(session["bookingSessionId"], lambda s: s["state"] in {bs.CONFIRMED, bs.FAILED})
    assert done["state"] == bs.CONFIRMED
    assert driver.otp_seen is None


# --------------------------------------------------------------------------
# Refusals - nothing is ever confirmed that was not confirmed
# --------------------------------------------------------------------------

def test_submission_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("OLADOC_SUBMIT_MODE", raising=False)

    class RealSubmitDriver(StubDriver):
        def submit_booking(self):
            if not bw.submit_enabled():
                raise bw.SubmitDisabled("disabled")
            return super().submit_booking()

    driver = RealSubmitDriver(needs_otp=False)
    session, _ = _start(driver)
    done = _wait_for(session["bookingSessionId"], lambda s: s["state"] == bs.FAILED)
    assert done["state"] == bs.FAILED
    assert done["error"] == "SUBMIT_DISABLED"


def test_identity_mismatch_stops_before_anything_is_filled(monkeypatch):
    monkeypatch.setenv("OLADOC_SUBMIT_MODE", "live")
    driver = StubDriver(mismatch=True)
    session, _ = _start(driver)
    done = _wait_for(session["bookingSessionId"], lambda s: s["state"] == bs.FAILED)
    assert done["error"] == "DOCTOR_IDENTITY_MISMATCH"
    assert driver.filled is None and driver.submitted is False


def test_taken_slot_fails_without_substituting_another(monkeypatch):
    monkeypatch.setenv("OLADOC_SUBMIT_MODE", "live")
    driver = StubDriver(slot_gone=True)
    session, _ = _start(driver)
    done = _wait_for(session["bookingSessionId"], lambda s: s["state"] == bs.FAILED)
    assert done["error"] == "SLOT_NO_LONGER_AVAILABLE"
    assert driver.submitted is False


def test_provider_declining_is_not_reported_as_confirmed(monkeypatch):
    monkeypatch.setenv("OLADOC_SUBMIT_MODE", "live")
    driver = StubDriver(needs_otp=False, confirm=False)
    session, _ = _start(driver)
    done = _wait_for(session["bookingSessionId"], lambda s: s["state"] == bs.FAILED)
    assert done["error"] == "PROVIDER_DID_NOT_CONFIRM"


def test_browser_is_always_closed(monkeypatch):
    monkeypatch.setenv("OLADOC_SUBMIT_MODE", "live")
    driver = StubDriver(mismatch=True)
    session, _ = _start(driver)
    _wait_for(session["bookingSessionId"], lambda s: s["state"] == bs.FAILED)
    time.sleep(0.2)
    assert driver.closed is True, "the driver must be closed even when the flow fails"


# --------------------------------------------------------------------------
# Appointment record
# --------------------------------------------------------------------------

def test_record_snapshots_doctor_and_clinic():
    record = appointments.build_record(
        doctor=DOCTOR, date="2026-09-21", time_24="15:30", time_label="03:30 PM",
        clinic=CLINIC, booking_status=appointments.IN_PROGRESS, slot_id="s1",
    )
    assert record["doctorId"] == "oladoc:2571726"
    assert record["providerDoctorId"] == "2571726"
    assert record["specialistName"] == "Dr. Ghulam Abbas"
    assert record["clinicName"] == "Akhtar Saeed Clinic"
    assert record["timezone"] == "Asia/Karachi"
    assert record["bookingStatus"] == appointments.IN_PROGRESS
    assert record["emailStatus"] == es.EMAIL_PENDING
    assert record["providerAppointmentId"] == ""


def test_record_starts_unconfirmed_and_only_the_provider_confirms_it():
    user = db.create_user({"fullName": "Ayesha Khan", "email": "a@example.com", "passwordHash": "x:y"})
    appointment = db.create_appointment(user["id"], appointments.build_record(
        doctor=DOCTOR, date="2026-09-21", time_24="15:30", time_label="03:30 PM", clinic=CLINIC,
    ))
    assert appointments.is_confirmed(appointment) is False
    assert appointments.status_label(appointment) == "Selected"

    updated = appointments.mark_confirmed(
        db, appointment["id"], user["id"], provider_appointment_id="OLA-55120",
    )
    assert appointments.is_confirmed(updated) is True
    assert appointments.status_label(updated) == "Confirmed"
    assert updated["providerAppointmentId"] == "OLA-55120"
    assert updated["status"] == "confirmed"  # legacy field stays in step


def test_confirmation_fields_omit_details_the_provider_never_gave():
    record = appointments.build_record(
        doctor=DOCTOR, date="2026-09-21", time_24="15:30", time_label="03:30 PM", clinic={},
    )
    labels = [label for label, _ in appointments.confirmation_fields(record, PATIENT)]
    assert "Clinic" not in labels
    assert "Provider reference" not in labels
    assert "Doctor" in labels and "Date" in labels


# --------------------------------------------------------------------------
# Email is independent of booking
# --------------------------------------------------------------------------

def test_email_is_not_sent_before_the_provider_confirms():
    user = db.create_user({"fullName": "B", "email": "b@example.com", "passwordHash": "x:y"})
    appointment = db.create_appointment(user["id"], appointments.build_record(
        doctor=DOCTOR, date="2026-09-21", time_24="15:30", time_label="03:30 PM", clinic=CLINIC,
    ))
    result = appointments.send_confirmation_email(db, appointment, user)
    assert result["status"] == es.EMAIL_PENDING
    assert "not confirmed" in result["detail"]


# --------------------------------------------------------------------------
# The provider window belongs to the patient
# --------------------------------------------------------------------------

def test_window_stays_open_when_the_patient_must_finish_the_booking(monkeypatch):
    """The run usually ends before the booking does.

    Automation stops at the slot the provider will not let it pick, but the
    patient can still finish on the page in front of them. Closing the window
    there is what made the browser appear to flash open and vanish.
    """
    monkeypatch.setenv("OLADOC_SUBMIT_MODE", "live")
    driver = StubDriver(slot_gone=True)
    session, _ = _start(driver)
    done = _wait_for(session["bookingSessionId"], lambda s: s["state"] == bs.FAILED)

    assert done["error"] == "SLOT_NO_LONGER_AVAILABLE"
    assert driver.closed is False, "the patient's window was closed from under them"
    assert done["providerWindowOpen"] is True


def test_window_closes_once_the_patient_says_what_happened(monkeypatch):
    monkeypatch.setenv("OLADOC_SUBMIT_MODE", "live")
    driver = StubDriver(slot_gone=True)
    session, _ = _start(driver)
    session_id = session["bookingSessionId"]
    _wait_for(session_id, lambda s: s["state"] == bs.FAILED)

    assert bw.close_window(session_id) is True
    assert driver.closed is True
    assert bs.registry.get(session_id)["providerWindowOpen"] is False


def test_closing_a_window_twice_is_harmless(monkeypatch):
    monkeypatch.setenv("OLADOC_SUBMIT_MODE", "live")
    driver = StubDriver(slot_gone=True)
    session, _ = _start(driver)
    session_id = session["bookingSessionId"]
    _wait_for(session_id, lambda s: s["state"] == bs.FAILED)

    assert bw.close_window(session_id) is True
    assert bw.close_window(session_id) is False


def test_window_is_closed_when_the_provider_shows_a_different_doctor(monkeypatch):
    """Never leave the patient on another doctor's booking page."""
    monkeypatch.setenv("OLADOC_SUBMIT_MODE", "live")
    driver = StubDriver(mismatch=True)
    session, _ = _start(driver)
    done = _wait_for(session["bookingSessionId"], lambda s: s["state"] == bs.FAILED)

    assert done["error"] == "DOCTOR_IDENTITY_MISMATCH"
    assert driver.closed is True
    assert not done["providerWindowOpen"]


def test_window_is_closed_once_the_provider_confirms(monkeypatch):
    """Nothing left to do on the provider's page, so do not leak the window."""
    monkeypatch.setenv("OLADOC_SUBMIT_MODE", "live")
    driver = StubDriver(needs_otp=False, confirm=True)
    session, _ = _start(driver)
    done = _wait_for(session["bookingSessionId"], lambda s: s["state"] == bs.CONFIRMED)

    assert driver.closed is True
    assert not done["providerWindowOpen"]


def test_patient_confirmed_booking_is_emailed_too(monkeypatch):
    """A booking the patient confirmed still gets its confirmation email.

    The provider never confirmed it, so nothing triggers the send from the
    worker; the checklist step is what sends it, and the gate must allow it.
    """
    sent = {}

    def fake_send(u, a):
        sent["to"] = a
        return {"status": es.EMAIL_SENT, "detail": "ok"}

    monkeypatch.setattr(es, "send_confirmation", fake_send)
    user = db.create_user({"fullName": "P", "email": "p2@example.com", "passwordHash": "x:y"})
    record = appointments.build_record(
        doctor=DOCTOR, date="2026-09-21", time_24="15:30", time_label="03:30 PM", clinic=CLINIC,
    )
    record["bookingStatus"] = appointments.CONFIRMED
    record["confirmationSource"] = "patient"
    appointment = db.create_appointment(user["id"], record)

    result = appointments.send_confirmation_email(db, appointment, user)
    assert result["status"] == es.EMAIL_SENT
    assert sent["to"]["confirmationSource"] == "patient"


def test_patient_confirmed_email_does_not_claim_the_provider_confirmed_it():
    """No fake success: the wording must credit the patient, not the clinic."""
    user = {"fullName": "P", "email": "p@example.com"}
    appointment = {
        "date": "2026-09-21", "timeLabel": "03:30 PM", "specialistName": "Dr. A",
        "confirmationSource": "patient",
    }
    message = es.build_confirmation_message(user, appointment)
    body = message.get_content()
    assert "confirmed by you" in body
    assert "did not see the" in body
    assert "Your appointment is confirmed." not in body
    assert message["Subject"].startswith("Appointment recorded")


def test_provider_confirmed_email_still_reads_as_confirmed():
    user = {"fullName": "P", "email": "p@example.com"}
    appointment = {
        "date": "2026-09-21", "timeLabel": "03:30 PM", "specialistName": "Dr. A",
        "confirmationSource": "provider",
    }
    message = es.build_confirmation_message(user, appointment)
    assert "Your appointment is confirmed." in message.get_content()
    assert message["Subject"].startswith("Appointment confirmed")


def test_failed_email_leaves_the_appointment_confirmed(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SMTP_PORT", "9")
    monkeypatch.setenv("SMTP_USER", "u")
    monkeypatch.setenv("SMTP_PASSWORD", "p")

    user = db.create_user({"fullName": "C", "email": "c@example.com", "passwordHash": "x:y"})
    appointment = db.create_appointment(user["id"], appointments.build_record(
        doctor=DOCTOR, date="2026-09-21", time_24="15:30", time_label="03:30 PM", clinic=CLINIC,
    ))
    appointments.mark_confirmed(db, appointment["id"], user["id"], provider_appointment_id="OLA-1")

    result = appointments.send_confirmation_email(db, db.find_appointment_for_user(appointment["id"], user["id"]), user)
    assert result["status"] == es.EMAIL_FAILED

    stored = db.find_appointment_for_user(appointment["id"], user["id"])
    assert stored["bookingStatus"] == appointments.CONFIRMED, "a failed email must not unbook the appointment"
    assert stored["emailStatus"] == es.EMAIL_FAILED
