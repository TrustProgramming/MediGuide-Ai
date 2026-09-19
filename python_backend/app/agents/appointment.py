from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict


def validate_appointment(appointment_date: str, appointment_time: str, reason: str, confirmed: bool = False) -> Dict[str, Any]:
    errors = []
    try:
        selected_date = date.fromisoformat(appointment_date)
    except (TypeError, ValueError):
        selected_date = None
        errors.append("Choose a valid appointment date.")
    if selected_date and selected_date < date.today():
        errors.append("Appointments cannot be booked in the past.")
    try:
        datetime.strptime(appointment_time, "%H:%M")
    except (TypeError, ValueError):
        errors.append("Choose a valid appointment time.")
    if not str(reason or "").strip():
        errors.append("Describe the reason for the visit.")
    if not confirmed:
        errors.append("Patient confirmation is required before booking.")
    return {"valid": not errors, "errors": errors}
