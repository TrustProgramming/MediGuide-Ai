"""Structured logging for the symptom -> RAG -> doctor -> booking flow.

Only identifiers, counts and outcomes are logged. Never credentials, never
patient free text, never the clinical content of a record.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

logger = logging.getLogger("mediguide.flow")


def configure() -> None:
    """Make the flow events visible.

    Uvicorn leaves the root logger at WARNING, so without this the INFO-level
    transition events would never be written anywhere.
    """
    import os

    level = os.getenv("MEDIGUIDE_FLOW_LOG_LEVEL", "INFO").strip().upper()
    logger.setLevel(getattr(logging, level, logging.INFO))
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    logger.propagate = False


SYMPTOM_NORMALIZED = "SYMPTOM_NORMALIZED"
RAG_QUERY_CREATED = "RAG_QUERY_CREATED"
DOCTOR_SEARCH_COMPLETED = "DOCTOR_SEARCH_COMPLETED"
DOCTOR_SELECTED = "DOCTOR_SELECTED"
APPOINTMENT_DOCTOR_SET = "APPOINTMENT_DOCTOR_SET"
OLADOC_DOCTOR_RESOLVED = "OLADOC_DOCTOR_RESOLVED"
OLADOC_DOCTOR_VERIFIED = "OLADOC_DOCTOR_VERIFIED"
BOOKING_STARTED = "BOOKING_STARTED"
BOOKING_COMPLETED = "BOOKING_COMPLETED"
DOCTOR_IDENTITY_MISMATCH = "DOCTOR_IDENTITY_MISMATCH"
BOOKING_FAILED = "BOOKING_FAILED"
RAG_FAILED = "RAG_FAILED"

# Anything whose key looks like one of these is dropped before a line is written.
_REDACT_HINTS = ("password", "token", "secret", "api_key", "apikey", "authorization", "email", "phone")


def _safe(payload: Dict[str, Any]) -> Dict[str, Any]:
    safe: Dict[str, Any] = {}
    for key, value in (payload or {}).items():
        if any(hint in str(key).lower() for hint in _REDACT_HINTS):
            safe[key] = "[redacted]"
        elif isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
        elif isinstance(value, (list, tuple)):
            safe[key] = [item for item in value if isinstance(item, (str, int, float, bool))]
        elif isinstance(value, dict):
            safe[key] = _safe(value)
        else:
            safe[key] = str(type(value).__name__)
    return safe


def event(name: str, **payload: Any) -> None:
    body = _safe(payload)
    level = logging.WARNING if name in {DOCTOR_IDENTITY_MISMATCH, BOOKING_FAILED, RAG_FAILED} else logging.INFO
    logger.log(level, "%s %s", name, json.dumps(body, sort_keys=True, default=str))
