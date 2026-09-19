"""Appointment records.

Doctor and clinic details are stored as snapshots taken at booking time, so an
old appointment still reads correctly even after the provider changes a doctor's
profile.

Booking status and email status are deliberately separate fields: a confirmed
appointment whose confirmation email failed is still confirmed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from . import email_service, flow_log

# Booking lifecycle as stored on the record.
SELECTED = "SELECTED"           # the patient chose a doctor/date/slot
IN_PROGRESS = "IN_PROGRESS"     # the provider flow is running
AWAITING_OTP = "AWAITING_OTP"   # the provider asked the patient to verify
CONFIRMED = "CONFIRMED"         # the provider confirmed it
FAILED = "FAILED"
CANCELLED = "CANCELLED"

BOOKING_STATUS_LABELS = {
    SELECTED: "Selected",
    IN_PROGRESS: "Booking in progress",
    AWAITING_OTP: "Waiting for your verification code",
    CONFIRMED: "Confirmed",
    FAILED: "Not booked",
    CANCELLED: "Cancelled",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_record(
    *,
    doctor: Dict[str, Any],
    date: str,
    time_24: str,
    time_label: str = "",
    reason: str = "",
    clinic: Optional[Dict[str, Any]] = None,
    timezone_name: str = "Asia/Karachi",
    booking_status: str = SELECTED,
    booking_session_id: str = "",
    slot_id: str = "",
) -> Dict[str, Any]:
    """The payload stored for an appointment, with identity and snapshots."""
    clinic = clinic or {}
    return {
        # --- identity -----------------------------------------------------
        "doctorId": str(doctor.get("doctor_id", "")),
        "providerDoctorId": str(doctor.get("provider_doctor_id", "")),
        "provider": str(doctor.get("provider", "oladoc")),
        "providerProfileUrl": str(doctor.get("oladoc_profile_url", "")),
        "bookingSessionId": booking_session_id,
        "slotId": slot_id,
        # --- snapshots ----------------------------------------------------
        "specialistName": str(doctor.get("doctor_name", "")),
        "doctorName": str(doctor.get("doctor_name", "")),
        "specialistSpecialty": str(doctor.get("specialty", "")),
        "clinicId": str(clinic.get("clinicId", "")),
        "clinicName": str(clinic.get("name", "")),
        "clinicAddress": str(clinic.get("address", "")),
        "clinicFee": str(clinic.get("fee", "")),
        # --- when ---------------------------------------------------------
        "date": date,
        "time": time_24,
        "timeLabel": time_label or time_24,
        "timezone": timezone_name,
        # --- state --------------------------------------------------------
        "reason": reason,
        "bookingType": "physical",
        "paymentMethod": "pay-at-clinic",
        "bookingStatus": booking_status,
        # `status` is the field the existing dashboard/cancel flow reads.
        "status": "confirmed" if booking_status == CONFIRMED else booking_status.lower(),
        "providerAppointmentId": "",
        "confirmationReference": "",
        # Who confirmed it: "provider", "patient", or empty when unconfirmed.
        # The confirmation card states this plainly rather than implying the
        # provider verified something it did not.
        "confirmationSource": "",
        "emailStatus": email_service.EMAIL_PENDING,
        "emailDetail": "",
        "createdAt": _now(),
        "updatedAt": _now(),
    }


def update_record(db: Any, appointment_id: str, user_id: str, **changes: Any) -> Optional[Dict[str, Any]]:
    """Persist a change to one appointment through the repository."""
    if "bookingStatus" in changes and "status" not in changes:
        changes["status"] = (
            "confirmed" if changes["bookingStatus"] == CONFIRMED else str(changes["bookingStatus"]).lower()
        )
    return db.update_appointment(appointment_id, user_id, changes)


def mark_confirmed(
    db: Any,
    appointment_id: str,
    user_id: str,
    *,
    provider_appointment_id: str = "",
    confirmation_reference: str = "",
) -> Optional[Dict[str, Any]]:
    """Record provider confirmation. Only called once the provider has confirmed."""
    appointment = update_record(
        db, appointment_id, user_id,
        bookingStatus=CONFIRMED,
        providerAppointmentId=provider_appointment_id,
        confirmationReference=confirmation_reference or provider_appointment_id,
    )
    if appointment:
        flow_log.event(
            flow_log.BOOKING_COMPLETED,
            appointment_id=appointment_id,
            doctor_id=appointment.get("doctorId"),
            has_provider_reference=bool(provider_appointment_id),
        )
    return appointment


def send_confirmation_email(db: Any, appointment: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    """Send the confirmation email and record its outcome separately.

    Never raises, and never downgrades the booking status: email delivery is a
    notification, not part of whether the appointment exists.
    """
    if appointment.get("bookingStatus") != CONFIRMED:
        return {"status": email_service.EMAIL_PENDING,
                "detail": "Not sent: this appointment is not confirmed yet."}
    result = email_service.send_confirmation(user, appointment)
    update_record(
        db, appointment["id"], appointment["userId"],
        emailStatus=result["status"], emailDetail=result["detail"],
    )
    return result


def is_confirmed(appointment: Dict[str, Any]) -> bool:
    return appointment.get("bookingStatus") == CONFIRMED


def confirmed_by(appointment: Dict[str, Any]) -> str:
    """Human wording for who confirmed the appointment."""
    return {
        "provider": "Confirmed by the provider",
        "patient": "Confirmed by you",
    }.get(str(appointment.get("confirmationSource", "")), "Confirmed")


def status_label(appointment: Dict[str, Any]) -> str:
    return BOOKING_STATUS_LABELS.get(str(appointment.get("bookingStatus", "")), "Selected")


def confirmation_fields(appointment: Dict[str, Any], user: Dict[str, Any]) -> list:
    """Rows for the checklist/confirmation card.

    Only fields that actually carry a value are returned, so the card never
    displays a detail the provider did not give us.
    """
    # Prefer what the patient confirmed on the checklist for this booking; fall
    # back to their stored profile.
    rows = [
        ("Patient", appointment.get("patientName") or user.get("fullName", "")),
        ("Doctor", appointment.get("specialistName", "")),
        ("Specialty", appointment.get("specialistSpecialty", "")),
        ("Clinic", appointment.get("clinicName", "")),
        ("Address", appointment.get("clinicAddress", "")),
        ("Date", appointment.get("date", "")),
        ("Time", f"{appointment.get('timeLabel', '')} ({appointment.get('timezone', '')})".strip()),
        ("Appointment type", "In-person visit" if appointment.get("bookingType") == "physical" else appointment.get("bookingType", "")),
        ("Payment", "Pay at clinic" if appointment.get("paymentMethod") == "pay-at-clinic" else appointment.get("paymentMethod", "")),
        ("Consultation fee", appointment.get("clinicFee", "")),
        ("Email", appointment.get("patientEmail") or user.get("email", "")),
        ("Phone", appointment.get("patientPhone") or user.get("phone", "")),
        ("Provider", appointment.get("provider", "")),
        ("Provider reference", appointment.get("providerAppointmentId", "")),
    ]
    return [(label, str(value).strip()) for label, value in rows if str(value).strip() and str(value).strip() != "()"]
