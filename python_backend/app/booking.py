from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional
from urllib.parse import urlparse


def build_official_booking_link(specialist: Dict[str, Any], reason: str, preferred_date: str = "", preferred_time: str = "", patient_name: str = "") -> Dict[str, Any]:
    profile_url = specialist.get("profileUrl")
    if not profile_url:
        raise ValueError("This specialist has no official profile URL on file.")

    parsed = urlparse(profile_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("This specialist's profile URL is not valid.")

    reference_id = f"MG-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    platform = specialist.get("source") or parsed.netloc.replace("www.", "")

    summary_lines = [
        f"MediGuide referral {reference_id}",
        f"Patient: {patient_name}" if patient_name else None,
        f"Doctor: {specialist.get('name')} ({specialist.get('specialty')})",
        f"Reason for visit: {reason}" if reason else None,
        f"Preferred date: {preferred_date}" if preferred_date else None,
        f"Preferred time: {preferred_time}" if preferred_time else None,
        f"Please confirm live availability directly with {platform} — MediGuide does not control their schedule.",
    ]

    summary = "\n".join(line for line in summary_lines if line)
    return {
        "referenceId": reference_id,
        "url": profile_url,
        "platform": platform,
        "summary": summary,
        "disclosure": (
            f"This opens {specialist.get('name')}'s real public profile on {platform} with MediGuide tracking "
            "parameters attached. It does not automatically confirm the appointment unless the provider or patient reports it back."
        ),
    }
