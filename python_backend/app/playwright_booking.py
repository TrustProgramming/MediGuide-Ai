from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from importlib import import_module
from typing import Any, Dict, Iterable, List, Optional

from . import doctor_identity as identity
from . import flow_log

logger = logging.getLogger(__name__)

try:
    sync_playwright = import_module("playwright.sync_api").sync_playwright
except Exception as exc:  # pragma: no cover - runtime dependency check only
    sync_playwright = None
    _PLAYWRIGHT_IMPORT_ERROR = exc
else:
    _PLAYWRIGHT_IMPORT_ERROR = None


_LIVE_SCHEDULE_CACHE: Dict[tuple[str, str], tuple[float, Dict[str, Any]]] = {}
_LIVE_SCHEDULE_CACHE_LOCK = Lock()
_LIVE_SCHEDULE_CACHE_SECONDS = 600
_LIVE_DOCTOR_CACHE: Dict[tuple[str, str, str], tuple[float, List[Dict[str, Any]]]] = {}
_LIVE_DOCTOR_CACHE_SECONDS = 300


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name, "").strip().lower()
    if value in {"", "0", "false", "no", "off"}:
        return default
    return value in {"1", "true", "yes", "on"} or bool(value)


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing {name}; configure the approved clinic booking portal before booking.")
    return value


def _normalize(selector: Optional[str]) -> str:
    if not selector:
        return ""
    text = str(selector).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1]
    return text.replace(r"\(", "(").replace(r"\)", ")")


def _is_patient_verification_selector(selector: str) -> bool:
    lowered = selector.lower()
    return any(term in lowered for term in ("otp", "captcha", "verification", "one-time", "one_time"))


def _fill_if_configured(page, env_name: str, value: Any) -> None:
    selector = _normalize(os.getenv(env_name, "").strip())
    if not selector or _is_patient_verification_selector(selector):
        return
    _fill_first_available(page, [selector], value)


def _first_locator(locator: Any) -> Any:
    first = getattr(locator, "first", locator)
    return first() if callable(first) else first


def _fill_first_available(page, selectors: Iterable[str], value: Any) -> bool:
    if value is None or str(value).strip() == "":
        return False
    for selector in selectors:
        normalized = _normalize(selector)
        if not normalized or _is_patient_verification_selector(normalized):
            continue
        try:
            locator = _first_locator(page.locator(normalized))
            if locator.count() > 0:
                text = str(value).strip()
                try:
                    locator.fill(text)
                except Exception:
                    locator.select_option(text)
                return True
        except Exception:
            pass
    return False


def _canonicalize_results(results: List[Dict[str, Any]], *, city: str = "") -> List[Dict[str, Any]]:
    """Attach the canonical identity to raw provider results.

    A result that cannot be given a stable identity is dropped rather than
    offered for booking, because a doctor we cannot verify is a doctor we could
    silently get wrong.
    """
    canonical: List[Dict[str, Any]] = []
    for item in results:
        try:
            doctor = identity.from_live_search_result(item, city=city)
        except identity.DoctorIdentityError as exc:
            logger.warning("Dropping live doctor result without a stable identity: %s", exc)
            continue
        canonical.append({**item, **doctor})
    return canonical


def search_live_oladoc_doctors(query: str = "", city: str = "Lahore", specialty: str = "") -> List[Dict[str, Any]]:
    """Live Oladoc doctor search.

    Delegates to :mod:`oladoc_provider`, which filters results by the specialty
    slug in the profile URL. That is what keeps diagnostic labs and other-city
    entries - which also live under ``/dr/`` - out of the doctor list.
    """
    from . import oladoc_provider

    try:
        return oladoc_provider.search_doctors(query=query, city=city, specialty=specialty)
    except oladoc_provider.ProviderError as exc:
        raise RuntimeError(str(exc)) from exc


def _click_first_matching(page, selectors: Iterable[str]) -> bool:
    for selector in selectors:
        normalized = _normalize(selector)
        if not normalized:
            continue
        try:
            locator = _first_locator(page.locator(normalized))
            if locator.count() > 0:
                locator.click(timeout=15000)
                return True
        except Exception:
            pass

    for label in ["Book appointment", "Confirm booking", "Book now", "Schedule visit"]:
        try:
            locator = page.get_by_role("button", name=label)
            if locator.count() > 0:
                _first_locator(locator).click(timeout=15000)
                return True
        except Exception:
            pass

    return False


def _normalize_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _click_visible_text(page, phrases: Iterable[str], exclude_phrases: Optional[Iterable[str]] = None) -> bool:
    include = [p.strip() for p in phrases if p and p.strip()]
    exclude = [p.strip() for p in (exclude_phrases or []) if p and p.strip()]
    selectors = ["button", "[role='button']", "label", "a", "input[type='radio']", "input[type='checkbox']", "div[role='button']"]
    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = locator.count()
        except Exception:
            continue
        for index in range(min(count, 200)):
            try:
                item = locator.nth(index)
                text = _normalize_text(item.inner_text() or item.get_attribute("aria-label") or item.get_attribute("title") or "")
                if not text:
                    continue
                if any(phrase in text for phrase in include) and not any(block in text for block in exclude):
                    item.click(timeout=15000)
                    return True
            except Exception:
                continue
    return False


def _select_patient_slot(page, date: str, time: str) -> bool:
    date_value = str(date or "").strip()
    time_value = str(time or "").strip()
    selected = False

    if date_value:
        requested = datetime.strptime(date_value, "%Y-%m-%d")
        date_tokens = {
            _normalize_text(date_value),
            _normalize_text(date_value.replace("-", "/")),
            _normalize_text(date_value.replace("-", " ")),
            _normalize_text(f"{requested.strftime('%b')} {requested.day}"),
            _normalize_text(f"{requested.strftime('%B')} {requested.day}"),
        }
        for selector in ["button", "[role='button']", "label", "a", "div[role='button']"]:
            try:
                locator = page.locator(selector)
                count = locator.count()
            except Exception:
                continue
            for index in range(min(count, 200)):
                try:
                    item = locator.nth(index)
                    text = _normalize_text(item.inner_text() or item.get_attribute("aria-label") or item.get_attribute("title") or "")
                    if not text:
                        continue
                    if any(token in text for token in date_tokens) and not any(exclude in text for exclude in ["pay", "otp", "captcha", "online", "card", "checkout"]):
                        item.click(timeout=15000)
                        selected = True
                        break
                except Exception:
                    continue
            if selected:
                break

    if time_value and not selected:
        time_tokens = {
            _normalize_text(time_value),
            _normalize_text(time_value.replace(".", ":")),
            _normalize_text(time_value.replace(" am", "am").replace(" pm", "pm")),
        }
        for selector in ["button", "[role='button']", "label", "a", "div[role='button']"]:
            try:
                locator = page.locator(selector)
                count = locator.count()
            except Exception:
                continue
            for index in range(min(count, 200)):
                try:
                    item = locator.nth(index)
                    text = _normalize_text(item.inner_text() or item.get_attribute("aria-label") or item.get_attribute("title") or "")
                    if not text:
                        continue
                    if any(token in text for token in time_tokens) and not any(exclude in text for exclude in ["pay", "otp", "captcha", "online", "card", "checkout"]):
                        item.click(timeout=15000)
                        return True
                except Exception:
                    continue
        return False

    return selected


def _select_payment_method(page) -> bool:
    return _click_visible_text(
        page,
        [
            "pay at clinic",
            "pay in clinic",
            "pay cash",
            "cash at clinic",
            "clinic payment",
            "pay by cash",
        ],
        exclude_phrases=[
            "pay online",
            "online payment",
            "card payment",
            "credit card",
            "debit card",
            "checkout",
            "stripe",
            "visa",
            "mastercard",
        ],
    )


def _best_effort_fill_patient(page, patient: Dict[str, Any]) -> None:
    values = {
        "name": str(patient.get("fullName") or "").strip(),
        "email": str(patient.get("email") or "").strip(),
        "phone": str(patient.get("phone") or "").strip(),
        "city": str(patient.get("city") or "").strip(),
        "preferredContact": str(patient.get("preferredContact") or "").strip(),
    }
    selectors = {
        "name": [os.getenv("CLINIC_NAME_SELECTOR", ""), "input[name*='name' i]", "input[id*='name' i]", "#patient_name", "#log-yourname"],
        "email": [os.getenv("CLINIC_EMAIL_SELECTOR", ""), "input[type='email']", "input[name*='email' i]", "input[id*='email' i]"],
        "phone": [os.getenv("CLINIC_PHONE_SELECTOR", ""), "input[type='tel']", "input[name*='phone' i]", "input[name*='mobile' i]", "input[id*='phone' i]"],
        "city": [os.getenv("CLINIC_CITY_SELECTOR", ""), "input[name*='city' i]", "input[id*='city' i]", "input[name*='location' i]"],
        "preferredContact": [os.getenv("CLINIC_CONTACT_SELECTOR", ""), "select[name*='contact' i]", "select[id*='contact' i]"],
    }
    for field, value in values.items():
        _fill_first_available(page, selectors[field], value)


def get_live_schedule(specialist: Dict[str, Any], requested_date: str) -> Dict[str, Any]:
    """Real bookable times for one doctor on one date, read by Playwright.

    Slots come from the provider's own booking page for the doctor's clinic.
    Nothing is generated: an empty list means the provider published nothing for
    that date.
    """
    from . import oladoc_provider

    doctor = specialist if specialist.get("doctor_id") else identity.from_specialist(specialist)
    try:
        clinics = oladoc_provider.get_doctor_clinics(doctor)
        physical = [clinic for clinic in clinics if not clinic.get("isVideo")] or clinics
        if not physical:
            return {
                "specialistId": specialist.get("id", doctor.get("doctor_id", "")),
                "specialistName": doctor.get("doctor_name", ""),
                "date": requested_date,
                "slots": [],
                "reason": "NO_CLINIC_PUBLISHED",
                "sourceUrl": identity.booking_url_for(doctor),
            }
        result = oladoc_provider.get_live_slots(doctor, requested_date, clinic_id=physical[0]["clinicId"])
    except oladoc_provider.ProviderError as exc:
        raise RuntimeError(str(exc)) from exc

    return {
        "specialistId": specialist.get("id", doctor.get("doctor_id", "")),
        "specialistName": doctor.get("doctor_name", ""),
        "date": requested_date,
        "slots": [slot["startLabel"] for slot in result["slots"]],
        "structuredSlots": result["slots"],
        "timezone": result["timezone"],
        "clinic": physical[0],
        "availableDates": result["availableDates"],
        "reason": result["reason"],
        "sourceUrl": result["sourceUrl"],
    }


def get_live_schedule_dates(specialist: Dict[str, Any], days: int = 30) -> List[Dict[str, Any]]:
    from datetime import date, timedelta

    requested_dates = [(date.today() + timedelta(days=offset)).isoformat() for offset in range(1, days + 1)]
    available_dates = []
    with ThreadPoolExecutor(max_workers=min(4, len(requested_dates))) as executor:
        futures = {executor.submit(get_live_schedule, specialist, requested_date): requested_date for requested_date in requested_dates}
        for future in as_completed(futures):
            try:
                schedule = future.result()
            except Exception:
                continue
            if schedule["slots"]:
                available_dates.append({"date": schedule["date"], "slots": schedule["slots"]})
    return sorted(available_dates, key=lambda item: item["date"])


def book_clinic_appointment(specialist: Dict[str, Any], date: str, time: str, reason: str, patient: Dict[str, Any]) -> Dict[str, Any]:
    if sync_playwright is None:
        raise RuntimeError(f"Playwright is not installed. Original error: {_PLAYWRIGHT_IMPORT_ERROR}")

    booking_url = os.getenv("OLADOC_BOOKING_URL", "").strip() or specialist.get("oladocProfileUrl") or specialist.get("oladocUrl")
    if not booking_url:
        booking_url = _required_env("CLINIC_BOOKING_URL")
    is_oladoc = "oladoc.com" in booking_url.lower()
    # Patient-only CAPTCHA or OTP must be completed in the visible browser by the
    # patient. Playwright never reads, enters, solves, or bypasses those controls.
    patient_verification = _env_flag("CLINIC_PATIENT_VERIFICATION", default=True)
    browser_headless = False if patient_verification else not _env_flag("CLINIC_BOOKING_HEADED", default=False)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=browser_headless)
        page = browser.new_page()
        page.goto(booking_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_load_state("networkidle", timeout=30000)

        if is_oladoc and not specialist.get("oladocProfileUrl"):
            _fill_if_configured(page, "OLADOC_SEARCH_SELECTOR", specialist.get("name", ""))
            _click_first_matching(page, [os.getenv("OLADOC_SEARCH_SUBMIT_SELECTOR", ""), "button:has-text('Search')"])
            try:
                page.get_by_text(specialist.get("name", ""), exact=False).first.click(timeout=10000)
            except Exception:
                pass
            _click_first_matching(page, ["a:has-text('Book Appointment')", "button:has-text('Book Appointment')"])
            try:
                page.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass

        _fill_if_configured(page, "CLINIC_REASON_SELECTOR", reason or f"Appointment with {specialist.get('name', 'Specialist')}")
        _fill_first_available(page, [os.getenv("CLINIC_REASON_SELECTOR", ""), "textarea[name*='reason' i]", "textarea[name*='symptom' i]", "textarea[id*='reason' i]"], reason or f"Appointment with {specialist.get('name', 'Specialist')}")

        if date:
            _fill_first_available(page, [os.getenv("CLINIC_DATE_SELECTOR", ""), "input[type='date']", "input[name*='date' i]", "#date", "[data-testid='date-slot']"], date)
            _select_patient_slot(page, date, "")

        if time:
            _fill_first_available(page, [os.getenv("CLINIC_TIME_SELECTOR", ""), "input[type='time']", "input[name*='time' i]", "#time", "[data-testid='time-slot']"], time)
            _select_patient_slot(page, "", time)

        _best_effort_fill_patient(page, patient)

        if not _click_first_matching(page, [
            os.getenv("CLINIC_SUBMIT_SELECTOR", ""),
            os.getenv("OLADOC_CONTINUE_SELECTOR", "") if is_oladoc else "",
            "button:has-text(\"Book appointment\")",
            "button:has-text(\"Confirm booking\")",
            "button:has-text(\"Book now\")",
            "button:has-text(\"Schedule visit\")",
        ]):
            raise RuntimeError("The clinic portal did not expose a visible booking action to continue the migration flow.")

        _select_payment_method(page)

        if not _click_first_matching(page, [
            "button:has-text(\"Book appointment\")",
            "button:has-text(\"Confirm booking\")",
            "button:has-text(\"Complete booking\")",
            "button:has-text(\"Proceed to confirmation\")",
        ]):
            raise RuntimeError("The clinic portal did not expose a final booking action after the payment selection step.")

        verification_pending = False
        if patient_verification:
            wait_seconds = int(os.getenv("OLADOC_PATIENT_WAIT_SECONDS", "300"))
            page.wait_for_timeout(1000)
            success_url = os.getenv("OLADOC_SUCCESS_URL_CONTAINS", "confirmation|success|thank")
            patterns = [item.strip() for item in success_url.split("|") if item.strip()]
            try:
                page.wait_for_function("patterns => patterns.some(pattern => window.location.href.toLowerCase().includes(pattern))", arg=patterns, timeout=wait_seconds * 1000)
            except Exception as exc:
                body_text = page.locator("body").inner_text(timeout=5000).lower()
                if any(marker in body_text for marker in ["otp", "one-time password", "verification code", "captcha"]):
                    verification_pending = True
                else:
                    raise RuntimeError(f"The clinic portal did not confirm the booking: {exc}") from exc

        browser.close()

    return {
        "providerId": "clinic",
        "providerName": "Clinic portal via Python Playwright",
        "bookingReference": f"PY-{re.sub(r'[^0-9A-Z]', '', specialist.get('id', 'CLINIC')).upper()}-{os.urandom(4).hex().upper()}",
        "status": "awaiting-patient-verification" if verification_pending else "booked",
        "message": (f"Details were filled for {specialist.get('name', 'specialist')} on {date} at {time}. Complete the OTP or CAPTCHA in the open clinic window." if verification_pending else f"Python Playwright booking flow was completed for {specialist.get('name', 'specialist')} on {date} at {time}."),
        "patient": patient,
        "specialist": specialist,
        "date": date,
        "time": time,
        "reason": reason,
        "bookingSource": "python-playwright",
    }


# ---------------------------------------------------------------------------
# Identity-verified booking
#
# The doctor is never re-discovered here. The canonical record built at search
# time carries the provider id / profile URL, we open that exact page, and we
# confirm the provider is showing that same doctor before and after filling the
# form. Anything else stops the flow.
# ---------------------------------------------------------------------------


class DoctorIdentityMismatch(RuntimeError):
    """The provider is showing a different doctor than the one the patient selected."""

    code = "DOCTOR_IDENTITY_MISMATCH"

    def __init__(self, message: str, *, selected: Dict[str, Any], observed: Dict[str, Any], reasons: List[str]):
        super().__init__(message)
        self.selected = selected
        self.observed = observed
        self.reasons = reasons

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": False,
            "error": self.code,
            "message": str(self),
            "reasons": self.reasons,
            "selectedDoctorId": self.selected.get("doctor_id"),
            "observedDoctorId": self.observed.get("doctor_id") or self.observed.get("provider_doctor_id"),
        }


class DoctorUnavailable(RuntimeError):
    """The selected doctor's page or slots could not be reached."""

    code = "DOCTOR_UNAVAILABLE"


def _first_text(page, getters: Iterable[Any]) -> str:
    """Try each locator strategy in order, returning the first non-empty text."""
    for getter in getters:
        try:
            locator = getter()
            if locator is None:
                continue
            first = _first_locator(locator)
            if first.count() == 0:
                continue
            text = first.inner_text(timeout=4000)
            cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
            if cleaned:
                return cleaned
        except Exception:
            continue
    return ""


def extract_oladoc_doctor_identity(page) -> Dict[str, Any]:
    """Read the doctor identity the provider is actually displaying.

    Prefers stable signals: the provider id embedded in the live URL, then the
    accessible page heading. Deliberately avoids positional selectors.
    """
    current_url = ""
    try:
        current_url = str(page.url or "")
    except Exception:
        current_url = ""

    name = _first_text(page, [
        lambda: page.get_by_role("heading", level=1),
        lambda: page.locator("[data-testid='doctor-name']"),
        lambda: page.locator("h1"),
    ])
    specialty = _first_text(page, [
        lambda: page.locator("[data-testid='doctor-specialty']"),
        lambda: page.locator("[itemprop='medicalSpecialty']"),
        lambda: page.locator("h1 + p"),
        lambda: page.locator("h2"),
    ])

    provider_doctor_id = identity.provider_doctor_id_from_url(current_url)
    return {
        "doctor_name": name,
        "normalized_name": identity.normalize_name(name),
        "specialty": specialty,
        "normalized_specialty": identity.normalize_specialty(specialty),
        "provider_doctor_id": provider_doctor_id,
        "oladoc_profile_url": current_url,
        "observed_url": current_url,
    }


def verify_page_doctor(page, doctor: Dict[str, Any], *, stage: str) -> Dict[str, Any]:
    """Compare the on-page doctor with the selected one, or raise DoctorIdentityMismatch."""
    observed = extract_oladoc_doctor_identity(page)
    result = identity.verify_identity(doctor, observed)
    if not result["match"]:
        flow_log.event(
            flow_log.DOCTOR_IDENTITY_MISMATCH,
            stage=stage,
            method=result["method"],
            selected_doctor_id=doctor.get("doctor_id"),
            observed_provider_doctor_id=observed.get("provider_doctor_id") or None,
        )
        raise DoctorIdentityMismatch(
            "The doctor displayed by the booking provider does not match the selected doctor.",
            selected=doctor,
            observed=observed,
            reasons=result["reasons"],
        )
    flow_log.event(
        flow_log.OLADOC_DOCTOR_VERIFIED,
        stage=stage,
        method=result["method"],
        doctor_id=doctor.get("doctor_id"),
    )
    return observed


def book_with_canonical_doctor(
    doctor: Dict[str, Any],
    date: str,
    time: str,
    reason: str,
    patient: Dict[str, Any],
) -> Dict[str, Any]:
    """Book the exact selected doctor on Oladoc.

    Opens that doctor's own profile URL (never a name search), verifies identity
    on arrival, fills the appointment details, verifies identity again
    immediately before the confirmation step, and leaves OTP/CAPTCHA entirely to
    the patient in the visible window.
    """
    if sync_playwright is None:
        raise RuntimeError(f"Playwright is not installed. Original error: {_PLAYWRIGHT_IMPORT_ERROR}")

    doctor_id = str(doctor.get("doctor_id") or "").strip()
    if not doctor_id:
        raise DoctorUnavailable("The selected doctor has no canonical id, so booking cannot proceed.")

    booking_url = identity.booking_url_for(doctor)
    if not booking_url:
        raise DoctorUnavailable(
            f"No Oladoc profile URL is on file for {doctor.get('doctor_name', 'this doctor')}, so the exact doctor cannot be opened."
        )

    flow_log.event(
        flow_log.BOOKING_STARTED,
        doctor_id=doctor_id,
        identity_strength=doctor.get("identity_strength"),
        has_profile_url=bool(doctor.get("oladoc_profile_url")),
    )

    # Patient-only CAPTCHA/OTP must be completed by the patient in a visible
    # browser. Playwright never reads, enters, solves or bypasses either.
    patient_verification = _env_flag("CLINIC_PATIENT_VERIFICATION", default=True)
    browser_headless = False if patient_verification else not _env_flag("CLINIC_BOOKING_HEADED", default=False)
    verification_pending = False

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=browser_headless)
        page = browser.new_page()
        try:
            try:
                page.goto(booking_url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_load_state("networkidle", timeout=30000)
            except Exception as exc:
                raise DoctorUnavailable(
                    f"The selected doctor's Oladoc page could not be opened: {exc}"
                ) from exc

            flow_log.event(
                flow_log.OLADOC_DOCTOR_RESOLVED,
                doctor_id=doctor_id,
                opened_profile_url=bool(doctor.get("oladoc_profile_url")),
            )

            # Gate 1: the provider must be showing this doctor before we touch anything.
            observed = verify_page_doctor(page, doctor, stage="profile_loaded")

            _fill_first_available(page, [
                os.getenv("CLINIC_REASON_SELECTOR", ""),
                "textarea[name*='reason' i]",
                "textarea[name*='symptom' i]",
                "textarea[id*='reason' i]",
            ], reason or f"Appointment with {doctor.get('doctor_name', 'the selected doctor')}")

            if date:
                _fill_first_available(page, [
                    os.getenv("CLINIC_DATE_SELECTOR", ""),
                    "input[type='date']", "input[name*='date' i]", "[data-testid='date-slot']",
                ], date)
                _select_patient_slot(page, date, "")
            if time:
                _fill_first_available(page, [
                    os.getenv("CLINIC_TIME_SELECTOR", ""),
                    "input[type='time']", "input[name*='time' i]", "[data-testid='time-slot']",
                ], time)
                _select_patient_slot(page, "", time)

            _best_effort_fill_patient(page, patient)

            if not _click_first_matching(page, [
                os.getenv("CLINIC_SUBMIT_SELECTOR", ""),
                os.getenv("OLADOC_CONTINUE_SELECTOR", ""),
                "button:has-text(\"Book appointment\")",
                "button:has-text(\"Confirm booking\")",
                "button:has-text(\"Book now\")",
                "button:has-text(\"Schedule visit\")",
            ]):
                raise DoctorUnavailable(
                    "Oladoc did not offer a booking action on this doctor's page. The doctor may have no bookable slots right now."
                )

            _select_payment_method(page)

            # Gate 2: re-verify immediately before the confirmation step, in case
            # the provider navigated us somewhere else mid-flow.
            verify_page_doctor(page, doctor, stage="pre_confirmation")

            if not _click_first_matching(page, [
                "button:has-text(\"Book appointment\")",
                "button:has-text(\"Confirm booking\")",
                "button:has-text(\"Complete booking\")",
                "button:has-text(\"Proceed to confirmation\")",
            ]):
                raise DoctorUnavailable(
                    "Oladoc did not offer a final booking action after the payment selection step."
                )

            if patient_verification:
                wait_seconds = int(os.getenv("OLADOC_PATIENT_WAIT_SECONDS", "300"))
                success_url = os.getenv("OLADOC_SUCCESS_URL_CONTAINS", "confirmation|success|thank")
                patterns = [item.strip() for item in success_url.split("|") if item.strip()]
                try:
                    page.wait_for_function(
                        "patterns => patterns.some(pattern => window.location.href.toLowerCase().includes(pattern))",
                        arg=patterns,
                        timeout=wait_seconds * 1000,
                    )
                except Exception as exc:
                    body_text = ""
                    try:
                        body_text = page.locator("body").inner_text(timeout=5000).lower()
                    except Exception:
                        body_text = ""
                    if any(marker in body_text for marker in ["otp", "one-time password", "verification code", "captcha"]):
                        verification_pending = True
                    else:
                        raise DoctorUnavailable(
                            f"Oladoc did not confirm the booking for the selected doctor: {exc}"
                        ) from exc
        finally:
            browser.close()

    flow_log.event(
        flow_log.BOOKING_COMPLETED,
        doctor_id=doctor_id,
        awaiting_patient_verification=verification_pending,
    )

    return {
        "success": True,
        "providerId": "oladoc",
        "providerName": "Oladoc (exact doctor profile via Playwright)",
        "doctorId": doctor_id,
        "doctorName": doctor.get("doctor_name", ""),
        "providerDoctorId": doctor.get("provider_doctor_id", ""),
        "profileUrl": booking_url,
        "verifiedDoctorName": observed.get("doctor_name", ""),
        "bookingReference": f"OLA-{re.sub(r'[^0-9A-Za-z]', '', doctor_id).upper()[:18]}-{os.urandom(3).hex().upper()}",
        "status": "awaiting-patient-verification" if verification_pending else "booked",
        "message": (
            f"Details were filled for {doctor.get('doctor_name', 'the selected doctor')} on {date} at {time}. "
            "Complete the OTP or CAPTCHA in the open Oladoc window."
            if verification_pending
            else f"Oladoc booking flow completed for {doctor.get('doctor_name', 'the selected doctor')} on {date} at {time}."
        ),
        "date": date,
        "time": time,
        "reason": reason,
        "bookingSource": "python-playwright-canonical-doctor",
    }
