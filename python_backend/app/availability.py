from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List

from .db import db

BOOKING_WINDOW_DAYS = 60
OPENING_HOUR = 9
CLOSING_HOUR = 17
SLOT_MINUTES = 30


def _parse_slot(slot_date: str, slot_time: str) -> datetime:
    return datetime.combine(date.fromisoformat(slot_date), time.fromisoformat(slot_time))


def is_slot_available(specialist_id: str, slot_date: str, slot_time: str, user_id: str | None = None) -> Dict[str, Any]:
    try:
        selected = _parse_slot(slot_date, slot_time)
    except ValueError:
        return {"available": False, "reason": "Choose a valid date and time."}
    today = date.today()
    if selected.date() < today or selected.date() > today + timedelta(days=BOOKING_WINDOW_DAYS):
        return {"available": False, "reason": f"Appointments are available within {BOOKING_WINDOW_DAYS} days."}
    if selected.weekday() >= 6 or selected.minute not in (0, 30) or not (OPENING_HOUR <= selected.hour < CLOSING_HOUR):
        return {"available": False, "reason": "The clinic is open Monday to Friday from 09:00 to 17:00 in 30-minute slots."}
    # Indexed lookup rather than scanning every appointment in memory.
    if db.is_slot_taken(specialist_id, slot_date, slot_time):
        return {"available": False, "reason": "This time has already been booked."}
    return {"available": True, "reason": "Slot is available."}


def list_slots(specialist_id: str, requested_date: str) -> List[str]:
    try:
        selected_date = date.fromisoformat(requested_date)
    except ValueError:
        return []
    slots = []
    current = datetime.combine(selected_date, time(OPENING_HOUR, 0))
    end = datetime.combine(selected_date, time(CLOSING_HOUR, 0))
    while current < end:
        formatted = current.strftime("%H:%M")
        if is_slot_available(specialist_id, requested_date, formatted)["available"]:
            slots.append(formatted)
        current += timedelta(minutes=SLOT_MINUTES)
    return slots
