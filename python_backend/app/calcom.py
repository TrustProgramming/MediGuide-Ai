"""Cal.com scheduling, and the confirmation email it sends as a side effect.

Cal.com has no general "send an email" endpoint: it is a scheduling service.
The only mail it sends on our behalf is the booking confirmation (plus a
calendar invite) delivered to the attendee when a booking is created against an
event type. So "email through Cal.com" means exactly this: mirror a confirmed
MediGuide appointment onto the Cal.com event type with the patient as attendee
and let Cal.com notify them.

Two faults in the earlier version are fixed here:

* Requests carried no User-Agent, so Cloudflare rejected every call with a 403
  (error 1010) before it reached the API at all.
* Cal.com API v1 is decommissioned; v2 requires an explicit `cal-api-version`.

The API key is read from the environment and never logged. Failures are
returned as values rather than raised into the booking flow: a confirmed
appointment stays confirmed even when the notification fails.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any, Dict, Optional

import requests

from .config import APP_BASE_URL, CAL_API_KEY, CAL_API_URL, CAL_EVENT_TYPE_ID

logger = logging.getLogger(__name__)

# Cal.com pins breaking changes behind a dated version header.
BOOKINGS_API_VERSION = "2024-08-13"
TIMEOUT_SECONDS = 20

# Cloudflare fronts api.cal.com and blocks unrecognised agents.
_HEADERS = {
    "User-Agent": "MediGuide/1.0 (appointment confirmation)",
    "Accept": "application/json",
    "Content-Type": "application/json",
}


def event_type_id() -> Optional[int]:
    """The configured event type, or None when unset or a placeholder.

    Cal.com event type ids are integers; the shipped example value is not, so a
    misconfigured deployment is reported rather than crashing on int().
    """
    raw = str(CAL_EVENT_TYPE_ID or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "CAL_EVENT_TYPE_ID is not a numeric Cal.com event type id; "
            "Cal.com notification is disabled."
        )
        return None


def configured() -> bool:
    return bool(CAL_API_KEY) and event_type_id() is not None


def calcom_status() -> Dict[str, Any]:
    return {
        "configured": configured(),
        "apiUrl": CAL_API_URL,
        "eventTypeId": event_type_id(),
    }


def _auth_headers(version: str = "") -> Dict[str, str]:
    headers = dict(_HEADERS)
    headers["Authorization"] = f"Bearer {CAL_API_KEY}"
    if version:
        headers["cal-api-version"] = version
    return headers


def _redact(text: str) -> str:
    """Keep the API key out of log lines and error messages."""
    return text.replace(CAL_API_KEY, "<redacted>") if CAL_API_KEY else text


def to_utc_iso(date: str, time_label: str, time_zone: str) -> str:
    """Combine an appointment's local date and time into a UTC instant.

    Cal.com takes the start time in UTC; the attendee's own timeZone travels
    separately so their invite renders in local time.
    """
    from zoneinfo import ZoneInfo

    cleaned = str(time_label or "").strip()
    parsed = None
    for fmt in ("%H:%M", "%I:%M %p", "%I:%M%p", "%H:%M:%S"):
        try:
            parsed = datetime.strptime(cleaned, fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        raise ValueError(f"Unrecognised appointment time: {time_label!r}")

    day = datetime.strptime(str(date).strip(), "%Y-%m-%d")

    # Windows has no system tz database. `tzdata` supplies one, but a
    # deployment missing it must still send rather than raise, so fall back to
    # a fixed offset for the clinic's own zone.
    local_zone = None
    for candidate in (time_zone, "Asia/Karachi"):
        if not candidate:
            continue
        try:
            local_zone = ZoneInfo(candidate)
            break
        except Exception:
            continue
    if local_zone is None:
        logger.warning(
            "No timezone database available; assuming UTC+05:00 for %r. "
            "Install tzdata for correct handling.", time_zone,
        )
        local_zone = dt_timezone(timedelta(hours=5))

    local = datetime(day.year, day.month, day.day, parsed.hour, parsed.minute, tzinfo=local_zone)
    return local.astimezone(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def create_calcom_booking(
    *,
    name: str,
    email: str,
    start: str,
    time_zone: str,
    metadata: Dict[str, Any],
    location: str = "",
) -> Dict[str, Any]:
    """Create a Cal.com booking. Raises RuntimeError with a redacted message."""
    type_id = event_type_id()
    if not CAL_API_KEY or type_id is None:
        raise RuntimeError(
            "Cal.com is not configured. Set CAL_API_KEY and a numeric CAL_EVENT_TYPE_ID."
        )
    payload = {
        "eventTypeId": type_id,
        "start": start,
        "attendee": {
            "name": name or "MediGuide patient",
            "email": email,
            "timeZone": time_zone or "Asia/Karachi",
            "language": "en",
        },
        # Cal.com rejects metadata values that are not strings.
        "metadata": {
            str(k): str(v)[:500] for k, v in metadata.items() if v not in (None, "")
        },
        # This event type is configured to ask the attendee where they will be.
        # The appointment happens at the clinic, so that is the address sent.
        "location": {
            "type": "attendeeAddress",
            "address": location or "Clinic address to be confirmed",
        },
    }
    try:
        response = requests.post(
            f"{CAL_API_URL}/bookings",
            data=json.dumps(payload),
            headers=_auth_headers(BOOKINGS_API_VERSION),
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Cal.com could not be reached ({type(exc).__name__}).") from exc

    if response.status_code >= 400:
        raise RuntimeError(
            f"Cal.com rejected the booking ({response.status_code}): "
            f"{_redact(response.text)[:400]}"
        )
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError("Cal.com returned a response that was not JSON.") from exc


def cancel_calcom_booking(booking_uid: str, reason: str = "Cancelled in MediGuide") -> Dict[str, Any]:
    """Cancel a booking previously created here."""
    if not CAL_API_KEY:
        raise RuntimeError("Cal.com is not configured.")
    response = requests.post(
        f"{CAL_API_URL}/bookings/{booking_uid}/cancel",
        data=json.dumps({"cancellationReason": reason}),
        headers=_auth_headers(BOOKINGS_API_VERSION),
        timeout=TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"Cal.com rejected the cancellation ({response.status_code}): "
            f"{_redact(response.text)[:300]}"
        )
    return response.json()


def notify_appointment(user: Dict[str, Any], appointment: Dict[str, Any]) -> Dict[str, Any]:
    """Mirror a confirmed appointment to Cal.com so it emails the patient.

    Returns a status record in the shape the SMTP sender uses, and never
    raises: the appointment's status must not depend on the notification.
    """
    attempted_at = datetime.now(dt_timezone.utc).isoformat()
    recipient = str(appointment.get("patientEmail") or user.get("email", "")).strip()

    if not recipient:
        return {
            "status": "FAILED",
            "detail": "No email address to notify.",
            "attemptedAt": attempted_at,
        }
    if not configured():
        return {
            "status": "NOT_CONFIGURED",
            "detail": "Cal.com is not configured. Set CAL_API_KEY and a numeric CAL_EVENT_TYPE_ID.",
            "attemptedAt": attempted_at,
        }

    try:
        start = to_utc_iso(
            str(appointment.get("date", "")),
            str(appointment.get("timeLabel") or appointment.get("time") or ""),
            str(appointment.get("timezone") or "Asia/Karachi"),
        )
    except ValueError as exc:
        return {"status": "FAILED", "detail": str(exc), "attemptedAt": attempted_at}

    doctor = appointment.get("specialistName") or appointment.get("doctorName") or ""
    try:
        result = create_calcom_booking(
            name=str(appointment.get("patientName") or user.get("fullName", "")),
            email=recipient,
            start=start,
            time_zone=str(appointment.get("timezone") or "Asia/Karachi"),
            location=str(
                appointment.get("clinicAddress")
                or appointment.get("clinicName")
                or ""
            ),
            metadata={
                "doctor": doctor,
                "specialty": appointment.get("specialistSpecialty", ""),
                "clinic": appointment.get("clinicName", ""),
                "reference": appointment.get("providerAppointmentId", ""),
                "mediguideId": appointment.get("id", ""),
                "returnUrl": f"{APP_BASE_URL}/ui",
            },
        )
    except RuntimeError as exc:
        # The message is already redacted by create_calcom_booking.
        logger.warning("Cal.com notification failed: %s", exc)
        return {"status": "FAILED", "detail": str(exc)[:300], "attemptedAt": attempted_at}

    data = result.get("data") or {}
    uid = str(data.get("uid") or data.get("id") or "")
    return {
        "status": "SENT",
        "detail": f"Cal.com booked the slot and emailed {recipient}.",
        "attemptedAt": attempted_at,
        "calcomBookingUid": uid,
    }
