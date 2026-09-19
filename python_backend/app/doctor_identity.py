"""Canonical doctor identity.

One doctor object is built the moment a doctor first appears (live Oladoc search
or the verified in-repo directory) and that same object is referenced by every
later stage: card, appointment form, booking API, Playwright, provider
verification. No layer re-derives a doctor from a name.

Identity strength, strongest first:

1. ``provider_doctor_id``  - Oladoc's own numeric id from the profile URL
2. ``oladoc_profile_url``  - the exact profile URL
3. normalized name + specialty (fallback only; never name alone)
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)

PROVIDER_OLADOC = "oladoc"

# https://oladoc.com/pakistan/lahore/dr/neurologist/hashir-amin-malik/3637096
_PROFILE_ID_PATTERN = re.compile(r"/dr/[^/]+/[^/]+/(\d+)", re.IGNORECASE)
_TRAILING_ID_PATTERN = re.compile(r"/(\d{4,})/?$")

_TITLE_PATTERN = re.compile(
    r"^\s*(?:assist\.?\s*prof\.?|assoc\.?\s*prof\.?|prof\.?|dr\.?|doctor)\s+",
    re.IGNORECASE,
)

_SPECIALTY_ALIASES = {
    "neurologist": "neurology",
    "neuro physician": "neurology",
    "neurophysician": "neurology",
    "cardiologist": "cardiology",
    "cardiology and emergency": "cardiology",
    "gastroenterologist": "gastroenterology",
    "dermatologist": "dermatology",
    "general physician": "primary care",
    "general practitioner": "primary care",
    "primary care physician": "primary care",
    "family physician": "primary care",
    "pediatrician": "pediatrics",
    "paediatrician": "pediatrics",
    "gynecologist": "gynecology",
    "gynaecologist": "gynecology",
    "ophthalmologist": "ophthalmology",
    "eye specialist": "ophthalmology",
    "orthopedic surgeon": "orthopedics",
    "orthopaedic surgeon": "orthopedics",
    "ent specialist": "ent",
    "ent surgeon": "ent",
}


class DoctorIdentityError(ValueError):
    """Raised when a doctor cannot be given a stable, verifiable identity."""


def provider_doctor_id_from_url(url: str) -> str:
    """Extract Oladoc's own doctor id from a profile URL, if the URL carries one."""
    text = str(url or "").strip()
    if not text:
        return ""
    match = _PROFILE_ID_PATTERN.search(text)
    if match:
        return match.group(1)
    match = _TRAILING_ID_PATTERN.search(urlsplit(text).path)
    return match.group(1) if match else ""


def normalize_url(url: str) -> str:
    """Canonical form for URL comparison: no scheme/query/fragment, no www, no trailing slash."""
    text = str(url or "").strip()
    if not text:
        return ""
    if "//" not in text:
        text = f"https://{text}"
    parts = urlsplit(text)
    host = (parts.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = (parts.path or "").rstrip("/").lower()
    return urlunsplit(("", host, path, "", "")).lstrip("/") or host


def normalize_name(name: str) -> str:
    """Lowercase, title-stripped, punctuation-free form used only for weak comparison."""
    text = str(name or "").strip()
    text = _TITLE_PATTERN.sub("", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def normalize_specialty(specialty: str) -> str:
    text = re.sub(r"\s+", " ", str(specialty or "").lower().replace("&", "and")).strip()
    text = text.rstrip(" .")
    return _SPECIALTY_ALIASES.get(text, text)


def _is_profile_url(url: str) -> bool:
    """A doctor profile URL, not a specialty directory listing page."""
    return "/dr/" in str(url or "").lower()


def build_doctor_id(provider: str, provider_doctor_id: str, profile_url: str, fallback_key: str = "") -> str:
    """Stable id. Never derived from the name alone."""
    if provider_doctor_id:
        return f"{provider}:{provider_doctor_id}"
    normalized = normalize_url(profile_url)
    if normalized and _is_profile_url(profile_url):
        return f"{provider}:url:{hashlib.sha1(normalized.encode('utf-8')).hexdigest()[:16]}"
    if fallback_key:
        return f"directory:{fallback_key}"
    raise DoctorIdentityError(
        "This doctor has no provider id and no profile URL, so a stable identity cannot be built."
    )


def canonical_doctor(
    *,
    name: str,
    specialty: str = "",
    location: str = "",
    profile_url: str = "",
    directory_url: str = "",
    source: str = "live_search",
    directory_key: str = "",
    rating_label: str = "",
    booking_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the one canonical record every later stage references.

    Raises DoctorIdentityError when the inputs cannot produce a stable identity,
    so a doctor without verifiable identity is never offered for booking.
    """
    clean_name = re.sub(r"\s+", " ", str(name or "").strip())
    if not clean_name:
        raise DoctorIdentityError("A doctor record must have a name.")

    profile = str(profile_url or "").strip()
    directory = str(directory_url or "").strip() or profile
    provider_doctor_id = provider_doctor_id_from_url(profile) or provider_doctor_id_from_url(directory)
    doctor_id = build_doctor_id(PROVIDER_OLADOC, provider_doctor_id, profile, directory_key)

    return {
        "doctor_id": doctor_id,
        "doctor_name": clean_name,
        "normalized_name": normalize_name(clean_name),
        "specialty": str(specialty or "").strip(),
        "normalized_specialty": normalize_specialty(specialty),
        "location": str(location or "").strip(),
        "source": source,
        "provider": PROVIDER_OLADOC,
        "provider_doctor_id": provider_doctor_id,
        "oladoc_profile_url": profile,
        "oladoc_directory_url": directory,
        "rating_label": str(rating_label or "").strip(),
        "identity_strength": identity_strength(provider_doctor_id, profile),
        "booking_metadata": dict(booking_metadata or {}),
    }


def identity_strength(provider_doctor_id: str, profile_url: str) -> str:
    if provider_doctor_id:
        return "provider_doctor_id"
    if profile_url and _is_profile_url(profile_url):
        return "profile_url"
    return "name_specialty"


def from_live_search_result(result: Dict[str, Any], *, city: str = "") -> Dict[str, Any]:
    """Canonicalize one scraped Oladoc search result."""
    return canonical_doctor(
        name=result.get("name", ""),
        specialty=result.get("specialty", ""),
        location=result.get("city", "") or city,
        profile_url=result.get("oladocProfileUrl", "") or result.get("oladocUrl", ""),
        directory_url=result.get("oladocUrl", ""),
        source="live_search",
        rating_label=result.get("ratingLabel", ""),
        booking_metadata={"payAtClinic": bool(result.get("payAtClinic", True))},
    )


def from_specialist(specialist: Dict[str, Any]) -> Dict[str, Any]:
    """Canonicalize a verified in-repo directory specialist."""
    return canonical_doctor(
        name=specialist.get("name", ""),
        specialty=specialist.get("specialty", ""),
        location=specialist.get("city", ""),
        profile_url=specialist.get("oladocProfileUrl", "") or specialist.get("profileUrl", ""),
        directory_url=specialist.get("oladocUrl", ""),
        source="directory",
        directory_key=str(specialist.get("id", "")),
        rating_label=str(specialist.get("rating", "")),
        booking_metadata={
            "specialistId": specialist.get("id", ""),
            "credentials": specialist.get("credentials", ""),
            "experience": specialist.get("experience", ""),
        },
    )


OLADOC_PROFILE_MARKER = "/dr/"


def oladoc_profile_url(doctor: Dict[str, Any]) -> str:
    """This doctor's own Oladoc profile, or "" when there isn't one.

    Oladoc profile pages look like
    ``/pakistan/lahore/dr/dermatologist/mehvish-hassan/675037`` - the ``/dr/``
    segment plus the trailing provider id identify one named doctor. A
    specialty landing page such as ``/pakistan/lahore/general-physician``
    identifies no one, so it is not a profile and is never treated as one.
    """
    url = str(doctor.get("oladoc_profile_url") or "").strip()
    if url and "oladoc.com" in url and OLADOC_PROFILE_MARKER in url:
        return url
    return ""


def is_bookable_on_oladoc(doctor: Dict[str, Any]) -> bool:
    """Whether Oladoc can actually be driven for this doctor.

    Requires a per-doctor identity: the provider's own id, or a profile URL
    that names one doctor. Without it there is nothing for Playwright to open,
    and guessing from the name could land on somebody else entirely - so this
    returns False and the caller tells the patient the truth instead.
    """
    if str(doctor.get("provider_doctor_id") or "").strip():
        return True
    return bool(oladoc_profile_url(doctor))


def booking_url_for(doctor: Dict[str, Any]) -> str:
    """The exact Oladoc page Playwright should open, or "" when there is none.

    Only a real Oladoc profile qualifies. A directory doctor's Marham page and
    a specialty landing page are both wrong targets: the first is a different
    provider, the second names no doctor at all.
    """
    return oladoc_profile_url(doctor)


def describe(doctor: Dict[str, Any]) -> Dict[str, Any]:
    """Safe-to-log identity fields. No patient data."""
    return {
        "doctor_id": doctor.get("doctor_id"),
        "provider_doctor_id": doctor.get("provider_doctor_id") or None,
        "specialty": doctor.get("normalized_specialty") or doctor.get("specialty"),
        "identity_strength": doctor.get("identity_strength"),
        "source": doctor.get("source"),
    }


def verify_identity(
    selected: Dict[str, Any],
    observed: Dict[str, Any],
    *,
    require_strong: bool = False,
) -> Dict[str, Any]:
    """Compare the doctor we selected against the doctor the provider actually shows.

    Returns {"match": bool, "method": str, "reasons": [...]}. A definitive
    identifier that disagrees is always a mismatch, even if the names match -
    duplicate names across different doctors are expected.
    """
    reasons: List[str] = []

    selected_provider_id = str(selected.get("provider_doctor_id") or "").strip()
    observed_provider_id = str(observed.get("provider_doctor_id") or "").strip()
    if selected_provider_id and observed_provider_id:
        if selected_provider_id == observed_provider_id:
            return {"match": True, "method": "provider_doctor_id", "reasons": ["Provider doctor id matched."]}
        return {
            "match": False,
            "method": "provider_doctor_id",
            "reasons": [
                f"Provider doctor id mismatch: selected {selected_provider_id}, provider showed {observed_provider_id}."
            ],
        }

    selected_url = normalize_url(selected.get("oladoc_profile_url", ""))
    observed_url = normalize_url(observed.get("oladoc_profile_url", ""))
    if selected_url and observed_url:
        if selected_url == observed_url:
            return {"match": True, "method": "profile_url", "reasons": ["Profile URL matched."]}
        reasons.append(f"Profile URL mismatch: expected {selected_url}, provider showed {observed_url}.")
        return {"match": False, "method": "profile_url", "reasons": reasons}

    if require_strong:
        return {
            "match": False,
            "method": "insufficient_identity",
            "reasons": ["No provider id or profile URL was available to verify this doctor."],
        }

    selected_name = selected.get("normalized_name") or normalize_name(selected.get("doctor_name", ""))
    observed_name = observed.get("normalized_name") or normalize_name(observed.get("doctor_name", ""))
    selected_specialty = selected.get("normalized_specialty") or normalize_specialty(selected.get("specialty", ""))
    observed_specialty = observed.get("normalized_specialty") or normalize_specialty(observed.get("specialty", ""))

    if not selected_name or not observed_name:
        return {"match": False, "method": "name_specialty", "reasons": ["A doctor name was missing on one side."]}
    if selected_name != observed_name:
        return {
            "match": False,
            "method": "name_specialty",
            "reasons": [f"Name mismatch: expected '{selected_name}', provider showed '{observed_name}'."],
        }
    # Same name is not enough - duplicate names belong to different doctors.
    if not selected_specialty or not observed_specialty:
        return {
            "match": False,
            "method": "name_specialty",
            "reasons": ["Names matched but no specialty was available to distinguish same-named doctors."],
        }
    if selected_specialty != observed_specialty:
        return {
            "match": False,
            "method": "name_specialty",
            "reasons": [
                f"Same name but different specialty: expected '{selected_specialty}', provider showed '{observed_specialty}'."
            ],
        }
    return {
        "match": True,
        "method": "name_specialty",
        "reasons": ["Name and specialty matched (weakest accepted identity)."],
    }


def find_duplicate_names(doctors: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Map normalized name -> doctor ids, for names shared by more than one doctor."""
    grouped: Dict[str, List[str]] = {}
    for doctor in doctors:
        key = doctor.get("normalized_name") or normalize_name(doctor.get("doctor_name", ""))
        grouped.setdefault(key, []).append(str(doctor.get("doctor_id", "")))
    return {name: ids for name, ids in grouped.items() if len(ids) > 1}
