"""Booking session state.

A booking spans several requests: the provider page is opened, patient details
are filled, the patient enters an OTP, and only then does the provider confirm.
This module holds the state for that span and pins it to one doctor, one date
and one slot.

The doctor, date and slot are written once at creation and are never changed by
a later call, so a session cannot drift onto a different doctor mid-flow.

The OTP itself is never stored, never logged and never read by the application.
It is typed by the patient into the provider's own page.
"""

from __future__ import annotations

import secrets
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from . import flow_log

# Lifecycle
CREATED = "CREATED"
DOCTOR_VERIFIED = "DOCTOR_VERIFIED"
SLOT_SELECTED = "SLOT_SELECTED"
PATIENT_DETAILS_FILLED = "PATIENT_DETAILS_FILLED"
OTP_REQUIRED = "OTP_REQUIRED"
OTP_VERIFIED = "OTP_VERIFIED"
BOOKING_SUBMITTED = "BOOKING_SUBMITTED"
CONFIRMED = "CONFIRMED"
FAILED = "FAILED"

TERMINAL_STATES = {CONFIRMED, FAILED}

# Only these transitions are allowed; anything else is a programming error.
ALLOWED_TRANSITIONS: Dict[str, set] = {
    CREATED: {DOCTOR_VERIFIED, FAILED},
    DOCTOR_VERIFIED: {SLOT_SELECTED, FAILED},
    SLOT_SELECTED: {PATIENT_DETAILS_FILLED, FAILED},
    PATIENT_DETAILS_FILLED: {OTP_REQUIRED, BOOKING_SUBMITTED, FAILED},
    OTP_REQUIRED: {OTP_VERIFIED, FAILED},
    OTP_VERIFIED: {BOOKING_SUBMITTED, FAILED},
    BOOKING_SUBMITTED: {CONFIRMED, FAILED},
    CONFIRMED: set(),
    FAILED: set(),
}

SESSION_TTL = timedelta(minutes=30)


class SessionError(RuntimeError):
    code = "BOOKING_SESSION_ERROR"


class InvalidTransition(SessionError):
    code = "INVALID_BOOKING_TRANSITION"


class _Registry:
    """In-process session store, safe for the app's threaded request handling."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: Dict[str, Dict[str, Any]] = {}

    def _prune(self) -> None:
        now = datetime.now(timezone.utc)
        expired = [
            key for key, value in self._sessions.items()
            if now - datetime.fromisoformat(value["createdAt"]) > SESSION_TTL
            and value["state"] not in TERMINAL_STATES
        ]
        for key in expired:
            self._sessions[key]["state"] = FAILED
            self._sessions[key]["error"] = "SESSION_EXPIRED"

    def create(
        self,
        *,
        user_id: str,
        doctor: Dict[str, Any],
        date: str,
        slot_id: str = "",
        slot_label: str = "",
        clinic_id: str = "",
        clinic_name: str = "",
    ) -> Dict[str, Any]:
        session_id = f"bk-{secrets.token_hex(10)}"
        record = {
            "bookingSessionId": session_id,
            "userId": user_id,
            # Identity fields are immutable for the life of the session.
            "doctorId": str(doctor.get("doctor_id", "")),
            "providerDoctorId": str(doctor.get("provider_doctor_id", "")),
            "doctorName": str(doctor.get("doctor_name", "")),
            "specialty": str(doctor.get("specialty", "")),
            "providerProfileUrl": str(doctor.get("oladoc_profile_url", "")),
            "clinicId": str(clinic_id),
            "clinicName": str(clinic_name),
            "date": str(date),
            "slotId": str(slot_id),
            "slotLabel": str(slot_label),
            "state": CREATED,
            # What the provider page accepted automatically, field by field, so
            # the patient is only asked to complete what genuinely failed.
            "autofill": {},
            "autofillAttempted": False,
            # The patient's own confirmation of the booking details, captured on
            # MediGuide rather than on the provider site.
            "checklist": {},
            "checklistComplete": False,
            # MediGuide cannot see inside the provider's window, so when the
            # automated run ends without a provider confirmation we ask the
            # patient what actually happened rather than guessing.
            # True while the provider's own browser window is still open for
            # the patient to finish the booking in.
            "providerWindowOpen": False,
            "patientOutcome": "",      # "" | "booked" | "not_booked"
            "patientReference": "",    # reference the patient read off Oladoc
            "history": [{"state": CREATED, "at": datetime.now(timezone.utc).isoformat()}],
            "error": "",
            "providerAppointmentId": "",
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            self._prune()
            self._sessions[session_id] = record
        flow_log.event(
            "BOOKING_SESSION_CREATED",
            booking_session_id=session_id, doctor_id=record["doctorId"],
            date=date, slot_id=slot_id,
        )
        return dict(record)

    def get(self, session_id: str, *, user_id: str = "") -> Optional[Dict[str, Any]]:
        with self._lock:
            self._prune()
            record = self._sessions.get(str(session_id))
            if record is None:
                return None
            if user_id and record["userId"] != user_id:
                return None  # a session belongs to exactly one account
            return dict(record)

    def transition(
        self,
        session_id: str,
        new_state: str,
        *,
        user_id: str = "",
        **updates: Any,
    ) -> Dict[str, Any]:
        """Move to a new state, refusing changes to the pinned identity."""
        forbidden = {"doctorId", "providerDoctorId", "date", "slotId", "userId"}
        illegal = forbidden & set(updates)
        if illegal:
            raise SessionError(f"A booking session cannot change {', '.join(sorted(illegal))} after creation.")

        with self._lock:
            record = self._sessions.get(str(session_id))
            if record is None:
                raise SessionError("Unknown booking session.")
            if user_id and record["userId"] != user_id:
                raise SessionError("This booking session belongs to a different account.")
            current = record["state"]
            if new_state != current and new_state not in ALLOWED_TRANSITIONS.get(current, set()):
                raise InvalidTransition(f"Cannot move a booking from {current} to {new_state}.")
            record.update({k: v for k, v in updates.items() if k not in forbidden})
            record["state"] = new_state
            record["updatedAt"] = datetime.now(timezone.utc).isoformat()
            record["history"].append({"state": new_state, "at": record["updatedAt"]})
            snapshot = dict(record)

        flow_log.event(
            "BOOKING_SESSION_STATE",
            booking_session_id=session_id, state=new_state, doctor_id=snapshot["doctorId"],
        )
        return snapshot

    def update(self, session_id: str, *, user_id: str = "", **updates: Any) -> Optional[Dict[str, Any]]:
        """Change session data without moving state, and without touching identity.

        Used for the autofill report and the patient's checklist, neither of
        which is a lifecycle transition.
        """
        forbidden = {"doctorId", "providerDoctorId", "date", "slotId", "userId", "state"}
        illegal = forbidden & set(updates)
        if illegal:
            raise SessionError(f"A booking session cannot change {', '.join(sorted(illegal))}.")
        with self._lock:
            record = self._sessions.get(str(session_id))
            if record is None:
                return None
            if user_id and record["userId"] != user_id:
                return None
            record.update(updates)
            record["updatedAt"] = datetime.now(timezone.utc).isoformat()
            return dict(record)

    def fail(self, session_id: str, error_code: str, *, user_id: str = "") -> Dict[str, Any]:
        return self.transition(session_id, FAILED, user_id=user_id, error=error_code)

    def for_user(self, user_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(v) for v in self._sessions.values() if v["userId"] == user_id]


registry = _Registry()


def summarize(session: Dict[str, Any]) -> Dict[str, Any]:
    """Safe-to-render view. Contains no OTP and no credentials."""
    return {
        "bookingSessionId": session["bookingSessionId"],
        "state": session["state"],
        "doctorId": session["doctorId"],
        "doctorName": session["doctorName"],
        "specialty": session["specialty"],
        "clinicName": session["clinicName"],
        "date": session["date"],
        "slotLabel": session["slotLabel"],
        "providerAppointmentId": session.get("providerAppointmentId", ""),
        "error": session.get("error", ""),
        "isConfirmed": session["state"] == CONFIRMED,
        "awaitingOtp": session["state"] == OTP_REQUIRED,
        "providerWindowOpen": bool(session.get("providerWindowOpen")),
        "patientOutcome": session.get("patientOutcome", ""),
        "patientReference": session.get("patientReference", ""),
        "autofill": dict(session.get("autofill") or {}),
        "autofillAttempted": bool(session.get("autofillAttempted")),
        "checklist": dict(session.get("checklist") or {}),
        "checklistComplete": bool(session.get("checklistComplete")),
        "providerReachable": session.get("state") not in {FAILED} or bool(session.get("autofill")),
    }


def needs_patient_outcome(session: Dict[str, Any]) -> bool:
    """True when we should ask the patient whether Oladoc booked the appointment.

    Asked only once the automated run has finished and the provider itself did
    not confirm. If the provider confirmed, there is nothing to ask.
    """
    if session.get("state") == CONFIRMED:
        return False
    if session.get("patientOutcome"):
        return False
    return session.get("state") in TERMINAL_STATES


def confirmation_source(session: Dict[str, Any]) -> str:
    """Who confirmed this booking: the provider, the patient, or nobody yet."""
    if session.get("state") == CONFIRMED:
        return "provider"
    if session.get("patientOutcome") == "booked":
        return "patient"
    return ""


CHECKLIST_FIELDS = (
    # key, label, whether the patient may edit it
    ("doctorName", "Doctor", False),
    ("specialty", "Specialty", False),
    ("date", "Appointment date", False),
    ("time", "Appointment time", False),
    ("clinicName", "Clinic / location", True),
    ("patientName", "Patient name", True),
    ("patientPhone", "Contact number", True),
    ("patientEmail", "Email", True),
    ("reason", "Reason for visit", True),
)


def seed_checklist(session: Dict[str, Any], patient: Dict[str, Any], reason: str = "") -> Dict[str, str]:
    """Pre-fill the checklist from what MediGuide already knows.

    The patient should never have to retype something the application already
    holds; they only confirm it.
    """
    return {
        "doctorName": session.get("doctorName", ""),
        "specialty": session.get("specialty", ""),
        "date": session.get("date", ""),
        "time": session.get("slotLabel", ""),
        "clinicName": session.get("clinicName", ""),
        "patientName": str(patient.get("fullName", "")),
        "patientPhone": str(patient.get("phone", "")),
        "patientEmail": str(patient.get("email", "")),
        "reason": reason,
    }


def checklist_missing(checklist: Dict[str, Any]) -> List[str]:
    """Labels of required details still blank. Reason for visit is optional."""
    optional = {"reason", "specialty"}
    return [
        label for key, label, _ in CHECKLIST_FIELDS
        if key not in optional and not str(checklist.get(key, "")).strip()
    ]
