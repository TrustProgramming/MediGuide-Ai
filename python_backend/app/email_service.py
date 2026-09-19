"""Appointment confirmation email.

Sent only after the provider has actually confirmed a booking. Email delivery is
tracked separately from booking status: a failed send never turns a confirmed
appointment into a failed one, and can be retried.

Credentials come from the environment (SMTP_HOST, SMTP_PORT, SMTP_USER,
SMTP_PASSWORD, SMTP_FROM). Nothing is hard-coded and message bodies are not
logged.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any, Dict, Optional

from . import flow_log

logger = logging.getLogger(__name__)

EMAIL_PENDING = "PENDING"
EMAIL_SENT = "SENT"
EMAIL_FAILED = "FAILED"
EMAIL_NOT_CONFIGURED = "NOT_CONFIGURED"


def confirmation_channel() -> str:
    """Which delivery channel to use: auto, smtp, calcom or both.

    `auto` prefers our own SMTP server and falls back to Cal.com, because an
    SMTP message is a plain confirmation whereas Cal.com additionally books a
    slot on the practice calendar. Set CONFIRMATION_CHANNEL to force one.
    """
    choice = os.getenv("CONFIRMATION_CHANNEL", "auto").strip().lower()
    return choice if choice in {"auto", "smtp", "calcom", "both"} else "auto"


def smtp_configured() -> bool:
    return all(os.getenv(name, "").strip() for name in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"))


def _format_date(value: str) -> str:
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%d %B %Y")
    except (TypeError, ValueError):
        return str(value or "")


def build_confirmation_message(user: Dict[str, Any], appointment: Dict[str, Any]) -> EmailMessage:
    """Compose the confirmation email from fields the provider actually returned."""
    recipient = str(user.get("email", "")).strip()
    doctor = appointment.get("specialistName") or appointment.get("doctorName") or "your doctor"
    specialty = appointment.get("specialistSpecialty") or ""
    clinic = appointment.get("clinicName") or ""
    date_label = _format_date(str(appointment.get("date", "")))
    time_label = appointment.get("timeLabel") or appointment.get("time") or ""
    reference = appointment.get("providerAppointmentId") or appointment.get("bookingReference") or ""
    provider = appointment.get("providerName") or appointment.get("providerId") or "the provider"

    # Say who confirmed it. An appointment the patient confirmed themselves is
    # not the same as one the provider verified, and the email must not blur
    # the two.
    source = str(appointment.get("confirmationSource", ""))
    if source == "patient":
        opening = [
            "You told us this appointment was booked on the provider's site.",
            "",
            "MediGuide has recorded it as confirmed by you. We did not see the",
            "provider confirm it, so please check with the clinic if anything",
            "below looks wrong.",
        ]
    else:
        opening = ["Your appointment is confirmed."]

    lines = opening + [
        "",
        f"Doctor:      {doctor}",
    ]
    if specialty:
        lines.append(f"Specialty:   {specialty}")
    if clinic:
        lines.append(f"Clinic:      {clinic}")
    lines += [
        f"Date:        {date_label}",
        f"Time:        {time_label} ({appointment.get('timezone', 'local clinic time')})",
        f"Patient:     {user.get('fullName', '')}",
        f"Booked via:  {provider}",
    ]
    if reference:
        lines.append(f"Reference:   {reference}")
    lines += [
        "",
        "Please arrive 15 minutes early and bring your identification, current",
        "medicines and any relevant reports. Payment is pay at clinic.",
        "",
        "MediGuide provides general health information and appointment support.",
        "It does not replace professional medical advice or emergency care.",
    ]

    message = EmailMessage()
    message["Subject"] = (
        f"Appointment recorded: {doctor} on {date_label}"
        if source == "patient" else
        f"Appointment confirmed: {doctor} on {date_label}"
    )
    message["From"] = os.getenv("SMTP_FROM", "").strip() or os.getenv("SMTP_USER", "").strip()
    message["To"] = recipient
    message.set_content("\n".join(lines))
    return message


def _send_via_calcom(user: Dict[str, Any], appointment: Dict[str, Any]) -> Dict[str, Any]:
    """Deliver through Cal.com.

    Cal.com exposes no general send-mail endpoint. Creating a booking on the
    configured event type is what makes it email the attendee, so this mirrors
    the appointment onto Cal.com rather than composing a message of our own.
    """
    from . import calcom

    if not calcom.configured():
        return {
            "status": EMAIL_NOT_CONFIGURED,
            "detail": "Cal.com is not configured. Set CAL_API_KEY and a numeric CAL_EVENT_TYPE_ID.",
            "attemptedAt": datetime.now(timezone.utc).isoformat(),
            "channel": "calcom",
        }
    result = calcom.notify_appointment(user, appointment)
    if result["status"] == "SENT":
        flow_log.event("CONFIRMATION_EMAIL_SENT", appointment_id=appointment.get("id"), channel="calcom")
        return {
            "status": EMAIL_SENT, "detail": result["detail"],
            "attemptedAt": result["attemptedAt"], "channel": "calcom",
            "calcomBookingUid": result.get("calcomBookingUid", ""),
        }
    flow_log.event("CONFIRMATION_EMAIL_FAILED", appointment_id=appointment.get("id"), channel="calcom")
    return {
        "status": EMAIL_FAILED if result["status"] == "FAILED" else EMAIL_NOT_CONFIGURED,
        "detail": result["detail"], "attemptedAt": result["attemptedAt"], "channel": "calcom",
    }


def _send_via_smtp(user: Dict[str, Any], appointment: Dict[str, Any]) -> Dict[str, Any]:
    """Deliver through our own SMTP server."""
    attempted_at = datetime.now(timezone.utc).isoformat()
    if not smtp_configured():
        return {
            "status": EMAIL_NOT_CONFIGURED,
            "detail": "SMTP is not configured. Set SMTP_HOST, SMTP_USER and SMTP_PASSWORD.",
            "attemptedAt": attempted_at, "channel": "smtp",
        }
    host = os.getenv("SMTP_HOST", "").strip()
    port = int(os.getenv("SMTP_PORT", "587") or 587)
    try:
        message = build_confirmation_message(user, appointment)
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=15, context=ssl.create_default_context()) as server:
                server.login(os.getenv("SMTP_USER", ""), os.getenv("SMTP_PASSWORD", ""))
                server.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=15) as server:
                try:
                    server.starttls(context=ssl.create_default_context())
                except smtplib.SMTPNotSupportedError:
                    logger.info("SMTP server does not advertise STARTTLS; continuing without it.")
                user_name = os.getenv("SMTP_USER", "")
                if user_name:
                    try:
                        server.login(user_name, os.getenv("SMTP_PASSWORD", ""))
                    except smtplib.SMTPNotSupportedError:
                        logger.info("SMTP server does not require authentication.")
                server.send_message(message)
        # Subject and body are deliberately not logged.
        flow_log.event("CONFIRMATION_EMAIL_SENT", appointment_id=appointment.get("id"), channel="smtp")
        return {
            "status": EMAIL_SENT, "detail": "Confirmation email accepted by the SMTP server.",
            "attemptedAt": attempted_at, "channel": "smtp",
        }
    except Exception as exc:
        flow_log.event("CONFIRMATION_EMAIL_FAILED", appointment_id=appointment.get("id"),
                       channel="smtp", error=type(exc).__name__)
        return {
            "status": EMAIL_FAILED,
            "detail": f"Email delivery failed ({type(exc).__name__}). The appointment itself is unaffected.",
            "attemptedAt": attempted_at, "channel": "smtp",
        }


def send_confirmation(user: Dict[str, Any], appointment: Dict[str, Any]) -> Dict[str, Any]:
    """Send the confirmation over the configured channel. Never raises.

    The caller stores the returned status alongside the appointment so booking
    success and email success stay independent.
    """
    recipient = str(appointment.get("patientEmail") or user.get("email", "")).strip()
    attempted_at = datetime.now(timezone.utc).isoformat()
    if not recipient:
        return {"status": EMAIL_FAILED, "detail": "The account has no email address.",
                "attemptedAt": attempted_at}

    channel = confirmation_channel()
    if channel == "smtp":
        return _send_via_smtp(user, appointment)
    if channel == "calcom":
        return _send_via_calcom(user, appointment)
    if channel == "both":
        smtp_result = _send_via_smtp(user, appointment)
        cal_result = _send_via_calcom(user, appointment)
        sent = [r for r in (smtp_result, cal_result) if r["status"] == EMAIL_SENT]
        return {
            "status": EMAIL_SENT if sent else EMAIL_FAILED,
            "detail": f"SMTP: {smtp_result['detail']} | Cal.com: {cal_result['detail']}",
            "attemptedAt": attempted_at,
            "channel": "both",
            "calcomBookingUid": cal_result.get("calcomBookingUid", ""),
        }

    # auto: our own SMTP first, Cal.com only if SMTP is unavailable.
    if smtp_configured():
        return _send_via_smtp(user, appointment)
    return _send_via_calcom(user, appointment)


def send_confirmation_for(appointment_id: str, db: Any) -> Dict[str, Any]:
    """Retry entry point: re-send for an appointment that is already confirmed."""
    appointment = db.find_appointment(appointment_id)
    if not appointment:
        return {"status": EMAIL_FAILED, "detail": "Appointment not found."}
    if appointment.get("bookingStatus") != "CONFIRMED":
        return {"status": EMAIL_FAILED, "detail": "Only a confirmed appointment gets a confirmation email."}
    user = db.find_user_by_id(appointment.get("userId", ""))
    if not user:
        return {"status": EMAIL_FAILED, "detail": "Account not found."}
    result = send_confirmation(user, appointment)
    db.update_appointment(appointment_id, appointment["userId"], {
        "emailStatus": result["status"], "emailDetail": result["detail"],
    })
    return result
