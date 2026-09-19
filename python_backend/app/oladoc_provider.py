"""Live Oladoc provider integration.

Everything here reads the real provider site with the project's existing
Playwright dependency. There is no second automation framework and no fabricated
data: if a slot is not on the page, it is not returned.

Page structure this relies on (verified against oladoc.com):

* listing   ``/pakistan/{city}/{specialty-slug}``
* profile   ``/pakistan/{city}/dr/{specialty-slug}/{doctor-slug}/{providerDoctorId}``
* booking   ``/appointment/{clinicId}/{providerDoctorId}``
* the booking page renders one ``.slot-date`` chip per bookable day, carrying the
  ISO date in its class attribute, and one ``.timing`` element per discrete slot.
"""

from __future__ import annotations

import logging
import re
import threading
import time as time_module
from contextlib import contextmanager
from datetime import date as date_cls
from importlib import import_module
from typing import Any, Dict, Iterator, List, Optional

from . import doctor_identity as identity
from . import flow_log

logger = logging.getLogger(__name__)

try:
    sync_playwright = import_module("playwright.sync_api").sync_playwright
except Exception as exc:  # pragma: no cover - dependency check only
    sync_playwright = None
    _PLAYWRIGHT_IMPORT_ERROR = exc
else:
    _PLAYWRIGHT_IMPORT_ERROR = None

OLADOC_ORIGIN = "https://oladoc.com"
PROVIDER_TIMEZONE = "Asia/Karachi"

# /pakistan/lahore/dr/urologist/usama-nawaz-ghumman/3217663
PROFILE_URL_RE = re.compile(
    r"/pakistan/(?P<city>[a-z0-9-]+)/dr/(?P<specialty>[a-z0-9-]+)/(?P<slug>[a-z0-9-]+)/(?P<pid>\d+)",
    re.IGNORECASE,
)
# /appointment/13580/3217663
BOOKING_URL_RE = re.compile(r"/appointment/(?P<clinic>\d+)/(?P<pid>\d+)")
TIME_RE = re.compile(r"^\s*(?:1[0-2]|0?[1-9]):[0-5]\d\s*(?:AM|PM)\s*$", re.IGNORECASE)
ISO_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")

# Categories that live under /dr/ but are not doctors.
NON_DOCTOR_CATEGORIES = {
    "pathology-lab", "radiology-lab", "lab", "laboratory", "hospital",
    "clinic", "pharmacy", "diagnostic-centre", "diagnostic-center",
}

SPECIALTY_SLUGS = {
    "primary care": "general-physician",
    "general physician": "general-physician",
    "general practitioner": "general-physician",
    "family physician": "general-physician",
    "gastroenterology": "gastroenterologist",
    "neurology": "neurologist",
    "cardiology": "cardiologist",
    "cardiology and emergency": "cardiologist",
    "cardiology & emergency": "cardiologist",
    "dermatology": "dermatologist",
    "ophthalmology": "ophthalmologist",
    "orthopedics": "orthopedic-surgeon",
    "orthopaedics": "orthopedic-surgeon",
    "gynecology": "gynecologist",
    "gynaecology": "gynecologist",
    "pediatrics": "pediatrician",
    "paediatrics": "pediatrician",
    "ent": "ent-specialist",
    "urology": "urologist",
    "psychiatry": "psychiatrist",
    "pulmonology": "pulmonologist",
    "nephrology": "nephrologist",
    "endocrinology": "endocrinologist",
    "oncology": "oncologist",
    "rheumatology": "rheumatologist",
    "dentistry": "dentist",
}

# Everyday phrasing -> the slug Oladoc actually uses.
SPECIALTY_SYNONYMS = {
    "heart specialist": "cardiologist", "heart doctor": "cardiologist",
    "heart": "cardiologist", "cardiac": "cardiologist",
    "skin specialist": "dermatologist", "skin doctor": "dermatologist", "skin": "dermatologist",
    "child specialist": "pediatrician", "children doctor": "pediatrician", "kids doctor": "pediatrician",
    "brain specialist": "neurologist", "nerve specialist": "neurologist",
    "stomach specialist": "gastroenterologist", "gut specialist": "gastroenterologist",
    "digestive": "gastroenterologist",
    "eye specialist": "ophthalmologist", "eye doctor": "ophthalmologist", "eyes": "ophthalmologist",
    "bone specialist": "orthopedic-surgeon", "bone doctor": "orthopedic-surgeon",
    "joint specialist": "orthopedic-surgeon", "orthopedic": "orthopedic-surgeon",
    "kidney specialist": "nephrologist", "kidney": "nephrologist",
    "urine specialist": "urologist", "urinary": "urologist", "urology specialist": "urologist",
    "ear nose throat": "ent-specialist", "ear specialist": "ent-specialist",
    "throat specialist": "ent-specialist", "nose specialist": "ent-specialist",
    "lung specialist": "pulmonologist", "chest specialist": "pulmonologist",
    "diabetes specialist": "endocrinologist", "hormone specialist": "endocrinologist",
    "mental health": "psychiatrist", "psychiatry specialist": "psychiatrist",
    "women doctor": "gynecologist", "lady doctor": "gynecologist",
    "cancer specialist": "oncologist", "tumor specialist": "oncologist",
    "general doctor": "general-physician", "family doctor": "general-physician",
    "physician": "general-physician", "gp": "general-physician",
    "teeth": "dentist", "tooth": "dentist", "dental": "dentist",
}


class ProviderError(RuntimeError):
    """The provider site could not be reached or read."""

    code = "PROVIDER_UNAVAILABLE"


class DoctorNotOnOladoc(ProviderError):
    """This doctor has no Oladoc profile, so Oladoc holds nothing to read.

    Not a failure of the provider or the network: there is simply no page for
    this doctor. MediGuide reads schedules and books through Oladoc only, so
    such a doctor cannot be scheduled here at all.
    """

    code = "DOCTOR_NOT_ON_OLADOC"


class ProviderTimeout(ProviderError):
    code = "PROVIDER_TIMEOUT"


def playwright_available() -> bool:
    return sync_playwright is not None


def _require_playwright() -> None:
    if sync_playwright is None:
        raise ProviderError(f"Playwright is not installed. Original error: {_PLAYWRIGHT_IMPORT_ERROR}")


@contextmanager
def browser_page(*, headless: bool = True, width: int = 1366, height: int = 1200) -> Iterator[Any]:
    """One place that owns browser lifecycle, so nothing leaks a process.

    The browser, context and page are always closed, including on error.
    """
    _require_playwright()
    browser = context = None
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(viewport={"width": width, "height": height})
            context.set_default_timeout(30000)
            page = context.new_page()
            yield page
        finally:
            for closeable in (context, browser):
                try:
                    if closeable is not None:
                        closeable.close()
                except Exception:  # pragma: no cover - best-effort cleanup
                    logger.debug("Ignoring error while closing a Playwright object", exc_info=True)


def city_slug(city: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(city or "Lahore").strip().lower()).strip("-") or "lahore"


def normalize_query(text: str) -> str:
    """Case-insensitive, whitespace-normalized, punctuation-trimmed."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", str(text or "").lower())).strip()


def specialty_slug(value: str) -> str:
    """Map free text to the slug Oladoc uses, tolerating common phrasing.

    Returns "" when the text does not look like a specialty, so the caller can
    treat it as a doctor-name search instead of guessing.
    """
    text = normalize_query(value)
    if not text:
        return ""
    if text in SPECIALTY_SLUGS:
        return SPECIALTY_SLUGS[text]
    if text in SPECIALTY_SYNONYMS:
        return SPECIALTY_SYNONYMS[text]
    collapsed = text.replace(" ", "-")
    known = set(SPECIALTY_SLUGS.values()) | set(SPECIALTY_SYNONYMS.values())
    if collapsed in known:
        return collapsed
    # "urology" -> "urologist", "cardiology" -> "cardiologist"
    for suffix, replacement in (("ology", "ologist"), ("iatrics", "iatrician")):
        if text.endswith(suffix):
            candidate = text[: -len(suffix)] + replacement
            if candidate in known:
                return candidate
    for phrase, slug in SPECIALTY_SYNONYMS.items():
        if phrase in text:
            return slug
    for name, slug in SPECIALTY_SLUGS.items():
        if name in text:
            return slug
    if collapsed.endswith(("ologist", "ician", "surgeon", "specialist", "dentist")):
        return collapsed
    return ""


def listing_url(city: str, slug: str) -> str:
    return f"{OLADOC_ORIGIN}/pakistan/{city_slug(city)}/{slug}"


def _parse_profile_href(href: str) -> Optional[Dict[str, str]]:
    match = PROFILE_URL_RE.search(str(href or ""))
    if not match:
        return None
    return {
        "city": match.group("city").lower(),
        "specialty_slug": match.group("specialty").lower(),
        "slug": match.group("slug"),
        "provider_doctor_id": match.group("pid"),
    }


def _absolute(href: str) -> str:
    href = str(href or "").strip()
    if href.startswith("http"):
        return href
    return f"{OLADOC_ORIGIN}{href}" if href.startswith("/") else href


def _slug_to_name(slug: str, specialty_slug: str = "") -> str:
    """Recover a doctor's name from their profile slug.

    Oladoc appends the specialty to a slug when two doctors share a name
    (``syed-raza-hussain-neurologist``); that suffix is not part of the name.
    """
    cleaned = re.sub(r"[-_]+", " ", str(slug or "")).strip()
    if specialty_slug:
        suffix = re.sub(r"[-_]+", " ", specialty_slug).strip().lower()
        if suffix and cleaned.lower().endswith(" " + suffix):
            cleaned = cleaned[: -(len(suffix) + 1)].strip()
    titled = " ".join(part.capitalize() for part in cleaned.split())
    return titled if titled.lower().startswith("dr") else f"Dr. {titled}"


# Link text on a listing card is not always the doctor's name - some cards
# expose the call-to-action instead. These are never names.
_NON_NAME_LABELS = {
    "view profile", "book appointment", "view", "profile", "book now",
    "book", "see profile", "view details", "details", "read more",
}


def _name_matches_slug(name: str, slug: str) -> bool:
    """Whether a scraped name plausibly belongs to this profile slug.

    The slug is part of the doctor's own URL, so it is the more trustworthy of
    the two. Sharing a real token is enough: titles and honorifics differ
    between the two ("Assist. Prof. Dr. Madiha Malik" vs ``madiha-malik``).
    """
    name_tokens = {t for t in re.findall(r"[a-z]{3,}", name.lower())
                   if t not in {"dr", "prof", "assist", "assoc", "mr", "mrs", "ms"}}
    slug_tokens = {t for t in re.findall(r"[a-z]{3,}", slug.lower())}
    return bool(name_tokens & slug_tokens)


# --------------------------------------------------------------------------
# Doctor search
# --------------------------------------------------------------------------

_SEARCH_CACHE: Dict[str, Any] = {}
_SEARCH_CACHE_LOCK = threading.Lock()
SEARCH_CACHE_SECONDS = 180


def search_doctors(
    query: str = "",
    city: str = "Lahore",
    specialty: str = "",
    *,
    limit: int = 30,
    use_cache: bool = True,
) -> List[Dict[str, Any]]:
    """Read real doctors from the Oladoc listing for a specialty.

    Only entries whose profile URL category matches the requested specialty are
    kept, which is what excludes the lab and diagnostic-centre links that share
    the ``/dr/`` path.
    """
    slug = specialty_slug(specialty) or specialty_slug(query)
    name_filter = ""
    if not slug:
        # Not a specialty - treat it as a doctor-name search within general practice.
        slug = "general-physician"
        name_filter = normalize_query(query)

    resolved_city = city_slug(city)
    cache_key = f"{resolved_city}|{slug}|{name_filter}|{limit}"
    if use_cache:
        with _SEARCH_CACHE_LOCK:
            hit = _SEARCH_CACHE.get(cache_key)
        if hit and time_module.monotonic() - hit[0] < SEARCH_CACHE_SECONDS:
            return [dict(item) for item in hit[1]]

    url = listing_url(resolved_city, slug)
    started = time_module.monotonic()
    try:
        with browser_page() as page:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            try:
                # "attached", not "visible": the first match is a hidden mobile
                # dropdown link, and the hrefs are read from the DOM anyway.
                page.wait_for_selector(f"a[href*='/dr/{slug}/']", state="attached", timeout=25000)
            except Exception as exc:
                raise ProviderTimeout(f"Oladoc did not render any doctor listings for {slug} in {resolved_city}.") from exc
            # Listings lazy-load; scroll until the count stops growing.
            previous = -1
            for _ in range(6):
                current = page.locator("a[href*='/dr/']").count()
                if current == previous:
                    break
                previous = current
                page.mouse.wheel(0, 5000)
                page.wait_for_timeout(1200)

            raw = page.evaluate(
                """() => Array.from(document.querySelectorAll("a[href*='/dr/']")).map(a => {
                    let card = a, text = '';
                    for (let i = 0; i < 6 && card; i++) {
                      card = card.parentElement;
                      if (card && card.innerText && card.innerText.trim().length > 40) {
                        text = card.innerText.trim();
                        break;
                      }
                    }
                    return {href: a.getAttribute('href') || '', linkText: (a.innerText || '').trim(), cardText: text};
                })"""
            )
    except ProviderError:
        raise
    except Exception as exc:
        raise ProviderError(f"The live Oladoc doctor search could not be loaded: {exc}") from exc

    doctors: Dict[str, Dict[str, Any]] = {}
    skipped_non_doctor = 0
    for entry in raw:
        parsed = _parse_profile_href(entry.get("href", ""))
        if not parsed:
            continue
        if parsed["specialty_slug"] in NON_DOCTOR_CATEGORIES or parsed["specialty_slug"] != slug:
            skipped_non_doctor += 1
            continue
        if parsed["city"] != resolved_city:
            continue

        provider_id = parsed["provider_doctor_id"]
        card_text = str(entry.get("cardText") or "")
        name = str(entry.get("linkText") or "").strip().splitlines()[0].strip() if entry.get("linkText") else ""
        # A card's link text is sometimes the button label ("View Profile")
        # rather than the doctor. The profile slug is part of the doctor's own
        # URL, so it decides whether the scraped text is really their name.
        if (not name or len(name) < 4 or not re.search(r"[A-Za-z]{3}", name)
                or name.strip().lower() in _NON_NAME_LABELS
                or not _name_matches_slug(name, parsed["slug"])):
            heading = re.search(r"(?:Assist\.?\s*Prof\.?\s*)?Dr\.?\s+[A-Z][A-Za-z.'\- ]{2,50}", card_text)
            if heading and _name_matches_slug(heading.group(0), parsed["slug"]):
                name = heading.group(0).strip()
            else:
                name = _slug_to_name(parsed["slug"], parsed["specialty_slug"])

        rating_match = re.search(r"\b(\d(?:\.\d)?)\s*(?:/\s*5|\(|\s+\d[\d,]*\s+Reviews)", card_text, re.I)
        rating = rating_match.group(1) if rating_match else ""
        clinic_match = re.search(r"\n([A-Z][^\n]{4,60}(?:Hospital|Clinic|Centre|Center|Lab|Complex))\b", card_text)
        experience = re.search(r"(\d+)\s*years?\s*experience", card_text, re.I)

        existing = doctors.get(provider_id)
        if existing and len(str(existing.get("cardText", ""))) >= len(card_text):
            continue

        profile_url = _absolute(entry["href"])
        doctors[provider_id] = {
            "name": name,
            "specialty": slug.replace("-", " ").title(),
            "city": resolved_city.title(),
            "rating": rating,
            "ratingLabel": f"{rating}/5" if rating else "Rating not shown",
            "clinic": clinic_match.group(1).strip() if clinic_match else "",
            "experience": f"{experience.group(1)} years" if experience else "",
            "oladocProfileUrl": profile_url,
            "oladocUrl": profile_url,
            "providerDoctorId": provider_id,
            "source": "Oladoc live",
            "payAtClinic": True,
            "cardText": card_text[:400],
        }

    results: List[Dict[str, Any]] = []
    for item in doctors.values():
        if name_filter and name_filter not in normalize_query(item["name"]):
            continue
        item.pop("cardText", None)
        try:
            canonical = identity.from_live_search_result(item, city=city)
        except identity.DoctorIdentityError:
            continue
        canonical["clinic"] = item.get("clinic", "")
        canonical["experience"] = item.get("experience", "")
        canonical["availability"] = "check_live"
        results.append({**item, **canonical})

    results.sort(key=lambda d: float(str(d.get("rating") or 0) or 0), reverse=True)
    results = results[:limit]

    flow_log.event(
        flow_log.DOCTOR_SEARCH_COMPLETED,
        specialty_slug=slug, city=resolved_city, results=len(results),
        skipped_non_doctor=skipped_non_doctor,
        elapsed_ms=int((time_module.monotonic() - started) * 1000),
        name_filtered=bool(name_filter),
    )
    if use_cache:
        with _SEARCH_CACHE_LOCK:
            _SEARCH_CACHE[cache_key] = (time_module.monotonic(), results)
    return [dict(item) for item in results]


# --------------------------------------------------------------------------
# Clinics and live schedule
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Default directory
# --------------------------------------------------------------------------

# The specialties the browse page is populated from. Every one is a real
# Oladoc listing slug, so every doctor shown has a real Oladoc profile and can
# actually be opened and booked. Nothing here is hard-coded doctor data.
DIRECTORY_SLUGS = (
    "general-physician",
    "dermatologist",
    "cardiologist",
    "gynecologist",
    "pediatrician",
    "orthopedic-surgeon",
    "neurologist",
    "ent-specialist",
)

DIRECTORY_CACHE_SECONDS = 900
_DIRECTORY_CACHE: Dict[str, Any] = {}
_DIRECTORY_CACHE_LOCK = threading.Lock()


def directory_doctors(
    city: str = "Lahore",
    *,
    per_specialty: int = 6,
    use_cache: bool = True,
) -> List[Dict[str, Any]]:
    """Live Oladoc doctors for the browse page, across several specialties.

    Oladoc is the only source: there is no local doctor list to fall back to,
    because a doctor MediGuide cannot open on Oladoc cannot be booked there
    either. Specialties are fetched in parallel since each is an independent
    page load, and the result is cached - a directory that takes ten seconds to
    rebuild on every visit is not usable.

    A specialty that fails is skipped rather than failing the whole page. If
    every one fails, ProviderError is raised so the caller can say so plainly
    instead of showing an empty directory that looks like "no doctors exist".
    """
    import concurrent.futures as futures

    cache_key = f"directory:{city_slug(city)}:{per_specialty}"
    if use_cache:
        with _DIRECTORY_CACHE_LOCK:
            hit = _DIRECTORY_CACHE.get(cache_key)
        if hit and time_module.monotonic() - hit[0] < DIRECTORY_CACHE_SECONDS:
            return list(hit[1])

    def fetch(slug: str) -> List[Dict[str, Any]]:
        try:
            return search_doctors(slug, city=city, use_cache=use_cache)[:per_specialty]
        except Exception as exc:
            logger.warning("Directory: specialty %s unavailable (%s)", slug, type(exc).__name__)
            return []

    doctors: List[Dict[str, Any]] = []
    failures = 0
    with futures.ThreadPoolExecutor(max_workers=len(DIRECTORY_SLUGS)) as pool:
        for slug, found in zip(DIRECTORY_SLUGS, pool.map(fetch, DIRECTORY_SLUGS)):
            if not found:
                failures += 1
            doctors.extend(found)

    if failures == len(DIRECTORY_SLUGS):
        raise ProviderError("Oladoc could not be reached, so no doctors can be listed.")

    # One doctor can be listed under more than one specialty.
    unique: Dict[str, Dict[str, Any]] = {}
    for doctor in doctors:
        key = str(doctor.get("provider_doctor_id") or doctor.get("doctor_id") or "")
        if key and key not in unique:
            unique[key] = doctor
    results = list(unique.values())

    with _DIRECTORY_CACHE_LOCK:
        _DIRECTORY_CACHE[cache_key] = (time_module.monotonic(), results)
    flow_log.event("OLADOC_DIRECTORY_LOADED", count=len(results), specialties_failed=failures)
    return results


def cached_directory_count(city: str = "Lahore", *, per_specialty: int = 6) -> Optional[int]:
    """How many doctors are in the warmed directory, without fetching.

    Returns None when nothing is cached yet, so a caller can say "loading"
    rather than claim there are zero doctors on Oladoc.
    """
    cache_key = f"directory:{city_slug(city)}:{per_specialty}"
    with _DIRECTORY_CACHE_LOCK:
        hit = _DIRECTORY_CACHE.get(cache_key)
    if hit and time_module.monotonic() - hit[0] < DIRECTORY_CACHE_SECONDS:
        return len(hit[1])
    return None


def get_doctor_clinics(doctor: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Booking destinations published on this doctor's own profile page.

    Each is ``/appointment/{clinicId}/{providerDoctorId}``, so the clinic and the
    doctor are both pinned by id.
    """
    profile_url = identity.booking_url_for(doctor)
    if not profile_url:
        raise DoctorNotOnOladoc(
            "This doctor does not have an Oladoc profile, so Oladoc has no "
            "schedule to read for them."
        )
    provider_id = str(doctor.get("provider_doctor_id") or "")

    try:
        with browser_page() as page:
            page.goto(profile_url, wait_until="domcontentloaded", timeout=45000)
            try:
                page.wait_for_selector("a[href*='/appointment/']", state="attached", timeout=25000)
            except Exception:
                return []
            raw = page.evaluate(
                """() => Array.from(document.querySelectorAll("a[href*='/appointment/']")).map(a => {
                    let block = a, text = '';
                    for (let i = 0; i < 5 && block; i++) {
                      block = block.parentElement;
                      if (block && block.innerText && block.innerText.trim().length > 30) {
                        text = block.innerText.trim(); break;
                      }
                    }
                    return {href: a.getAttribute('href') || '', label: (a.innerText || '').trim(), text: text};
                })"""
            )
    except ProviderError:
        raise
    except Exception as exc:
        raise ProviderError(f"The doctor's Oladoc profile could not be read: {exc}") from exc

    clinics: Dict[str, Dict[str, Any]] = {}
    for entry in raw:
        match = BOOKING_URL_RE.search(entry.get("href", ""))
        if not match:
            continue
        if provider_id and match.group("pid") != provider_id:
            continue  # never follow a link that points at a different doctor
        clinic_id = match.group("clinic")
        if clinic_id in clinics:
            continue
        text = str(entry.get("text") or "")
        name = ""
        for line in text.splitlines():
            line = line.strip()
            if line and not line.lower().startswith(("fee", "address", "available", "book", "pay")):
                name = line
                break
        fee = re.search(r"Rs\.?\s*[\d,]+", text)
        address = re.search(r"Address:\s*\n?\s*(.+)", text)
        clinics[clinic_id] = {
            "clinicId": clinic_id,
            "name": name or f"Clinic {clinic_id}",
            "bookingUrl": _absolute(entry["href"]),
            "fee": fee.group(0) if fee else "",
            "address": address.group(1).strip()[:160] if address else "",
            "isVideo": "video" in str(entry.get("label", "")).lower(),
        }
    return list(clinics.values())


def _read_slots_for_active_date(page) -> List[str]:
    """Discrete times currently rendered, in page order."""
    values = page.evaluate(
        """() => Array.from(document.querySelectorAll('.timing'))
             .filter(el => el.children.length === 0)
             .map(el => (el.textContent || '').trim())"""
    )
    out: List[str] = []
    for value in values:
        if TIME_RE.match(value or "") and value not in out:
            out.append(value.strip())
    return out


def _to_24h(value: str) -> str:
    match = re.match(r"\s*(\d{1,2}):(\d{2})\s*(AM|PM)\s*$", value, re.IGNORECASE)
    if not match:
        return ""
    hour, minute, meridiem = int(match.group(1)), match.group(2), match.group(3).upper()
    if meridiem == "PM" and hour != 12:
        hour += 12
    if meridiem == "AM" and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute}"


def get_live_slots(
    doctor: Dict[str, Any],
    requested_date: str,
    *,
    clinic_id: str = "",
    booking_url: str = "",
) -> Dict[str, Any]:
    """Extract the real bookable slots for one doctor, one clinic, one date.

    The returned ``slots`` are exactly the times Playwright read from the
    provider's booking page for the requested date. Nothing is generated.
    """
    try:
        date_cls.fromisoformat(requested_date)
    except (TypeError, ValueError) as exc:
        raise ValueError("Choose a valid date in YYYY-MM-DD form.") from exc

    provider_id = str(doctor.get("provider_doctor_id") or "").strip()
    target = booking_url.strip()
    if not target:
        if not clinic_id or not provider_id:
            raise ProviderError("A clinic and provider doctor id are required to read the live schedule.")
        target = f"{OLADOC_ORIGIN}/appointment/{clinic_id}/{provider_id}"

    match = BOOKING_URL_RE.search(target)
    if match and provider_id and match.group("pid") != provider_id:
        raise ProviderError("The booking URL does not belong to the selected doctor.")

    started = time_module.monotonic()
    try:
        with browser_page() as page:
            page.goto(target, wait_until="domcontentloaded", timeout=45000)
            try:
                page.wait_for_selector(".slot-date", timeout=25000)
            except Exception as exc:
                raise ProviderTimeout("Oladoc did not render a schedule for this doctor.") from exc

            observed_name = ""
            try:
                heading = page.locator("h1, h2").first
                if heading.count():
                    observed_name = (heading.inner_text(timeout=4000) or "").strip()
            except Exception:
                observed_name = ""

            chips = page.evaluate(
                """() => Array.from(document.querySelectorAll('.slot-date')).map((el, i) => ({
                    index: i, cls: el.className || '', text: (el.textContent || '').trim()
                }))"""
            )
            available_dates: List[str] = []
            target_index = -1
            for chip in chips:
                found = ISO_DATE_RE.search(chip["cls"])
                if not found:
                    continue
                available_dates.append(found.group(1))
                if found.group(1) == requested_date:
                    target_index = chip["index"]

            if target_index < 0:
                return {
                    "doctorId": doctor.get("doctor_id", ""),
                    "providerDoctorId": provider_id,
                    "clinicId": clinic_id or (match.group("clinic") if match else ""),
                    "date": requested_date,
                    "timezone": PROVIDER_TIMEZONE,
                    "slots": [],
                    "availableDates": available_dates[:45],
                    "sourceUrl": target,
                    "observedDoctorName": observed_name,
                    "reason": "DATE_NOT_OFFERED",
                    "message": "Oladoc is not offering this date for this clinic.",
                }

            chip = page.locator(".slot-date").nth(target_index)
            chip.scroll_into_view_if_needed(timeout=8000)
            before = _read_slots_for_active_date(page)
            chip.click(timeout=15000)
            # Wait for the chip to actually become the active one, rather than sleeping.
            try:
                page.wait_for_function(
                    """([index, iso]) => {
                        const el = document.querySelectorAll('.slot-date')[index];
                        return !!el && el.className.includes('active') && el.className.includes(iso);
                    }""",
                    arg=[target_index, requested_date],
                    timeout=12000,
                )
            except Exception:
                logger.debug("Date chip did not report active state; continuing to read slots")
            page.wait_for_timeout(1200)
            slots = _read_slots_for_active_date(page)
            if slots == before and requested_date != available_dates[0:1][0:1] and len(before) and target_index != 0:
                # The list did not change; re-read once after a short settle.
                page.wait_for_timeout(1500)
                slots = _read_slots_for_active_date(page)
    except (ProviderError, ValueError):
        raise
    except Exception as exc:
        raise ProviderError(f"The live schedule could not be read: {exc}") from exc

    structured = [
        {
            "slotId": f"{provider_id}:{clinic_id or (match.group('clinic') if match else '')}:{requested_date}:{_to_24h(value)}",
            "start": f"{requested_date}T{_to_24h(value)}",
            "startLabel": value,
            "time24": _to_24h(value),
            "end": "",
            "available": True,
            "source": "oladoc",
        }
        for value in slots
        if _to_24h(value)
    ]

    result = {
        "doctorId": doctor.get("doctor_id", ""),
        "providerDoctorId": provider_id,
        "clinicId": clinic_id or (match.group("clinic") if match else ""),
        "date": requested_date,
        "timezone": PROVIDER_TIMEZONE,
        "slots": structured,
        "availableDates": available_dates[:45],
        "sourceUrl": target,
        "observedDoctorName": observed_name,
        "reason": "" if structured else "NO_SLOTS_PUBLISHED",
        "message": "" if structured else "Oladoc published no bookable times for this date at this clinic.",
        "elapsedMs": int((time_module.monotonic() - started) * 1000),
    }
    flow_log.event(
        "LIVE_SLOTS_EXTRACTED",
        doctor_id=doctor.get("doctor_id"), provider_doctor_id=provider_id,
        date=requested_date, slot_count=len(structured), elapsed_ms=result["elapsedMs"],
    )
    return result
