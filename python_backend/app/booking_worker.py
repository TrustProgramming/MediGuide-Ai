"""Runs a booking across several requests, holding one browser per session.

The provider flow cannot complete inside a single HTTP request: the patient has
to read an OTP from their own phone and type it in. So the browser is owned by a
worker thread that pauses at the OTP step and waits to be handed a code.

The driver is injected, which keeps the state machine testable without
contacting a provider.

Safety: the final submit is disabled unless OLADOC_SUBMIT_MODE=live. With it
disabled the flow runs all the way to the submit step and then stops with
SUBMIT_DISABLED - it never reports a confirmation that did not happen.

The OTP is never stored on the session, never written to the record and never
logged. It lives only in memory for the moment it is typed into the provider's
own page.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from typing import Any, Callable, Dict, Optional, Protocol

from . import booking_session as bs
from . import doctor_identity as identity
from . import flow_log

logger = logging.getLogger(__name__)

OTP_WAIT_SECONDS = int(os.getenv("OLADOC_OTP_WAIT_SECONDS", "300"))
STEP_TIMEOUT_SECONDS = 120


class SubmitDisabled(RuntimeError):
    code = "SUBMIT_DISABLED"


class DoctorMismatch(RuntimeError):
    code = "DOCTOR_IDENTITY_MISMATCH"


def submit_enabled() -> bool:
    """Real submission is opt-in, so a test run can never create a real booking."""
    return os.getenv("OLADOC_SUBMIT_MODE", "disabled").strip().lower() == "live"


class BookingDriver(Protocol):
    """What the worker needs from a provider. Implemented for Oladoc, stubbed in tests."""

    def open_and_verify(self, doctor: Dict[str, Any]) -> Dict[str, Any]: ...
    def select_slot(self, date: str, time_label: str) -> bool: ...
    def fill_patient(self, patient: Dict[str, Any]) -> Dict[str, Any]: ...
    def otp_required(self) -> bool: ...
    def submit_otp(self, code: str) -> bool: ...
    def submit_booking(self) -> Dict[str, Any]: ...
    def close(self) -> None: ...


def _minutes_of_day(label: str) -> Optional[int]:
    """Minutes since midnight for a written time, or None if unreadable.

    Oladoc publishes "06:00 PM" while MediGuide may hold "18:00" for the same
    slot. Comparing the written form makes those two look like different times
    and the booking stops on a slot that is in fact available, so both sides are
    reduced to a number before they are compared.
    """
    text = str(label or "").strip().upper().replace(".", "")
    match = re.match(r"^(\d{1,2}):(\d{2})\s*(AM|PM)?$", text)
    if not match:
        return None
    hour, minute, meridiem = int(match.group(1)), int(match.group(2)), match.group(3)
    if minute > 59:
        return None
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if meridiem == "PM" else 0)
    elif not 0 <= hour <= 23:
        return None
    return hour * 60 + minute


class OladocDriver:
    """Drives the real Oladoc booking page with Playwright."""

    def __init__(self, booking_url: str = ""):
        self.booking_url = booking_url
        # When no URL is known yet the worker resolves the clinic first.
        self.needs_clinic = not booking_url
        self._stack = None
        self.page = None

    def _start(self):
        from contextlib import ExitStack

        from .oladoc_provider import browser_page

        self._stack = ExitStack()
        # Visible by default: the patient must be able to read and answer any
        # challenge the provider shows. Headless is only for automated runs.
        headless = os.getenv("OLADOC_BOOKING_HEADLESS", "false").strip().lower() in {"1", "true", "yes"}
        self.page = self._stack.enter_context(browser_page(headless=headless))

    def open_and_verify(self, doctor: Dict[str, Any]) -> Dict[str, Any]:
        if self.page is None:
            self._start()
        self.page.goto(self.booking_url, wait_until="domcontentloaded", timeout=60000)
        try:
            self.page.wait_for_selector(".slot-date", state="attached", timeout=25000)
        except Exception:
            pass

        observed_id = identity.provider_doctor_id_from_url(self.page.url) or ""
        name = ""
        for selector in ("h1", "h2"):
            try:
                locator = self.page.locator(selector).first
                if locator.count():
                    name = (locator.inner_text(timeout=4000) or "").strip()
                    break
            except Exception:
                continue

        observed = {
            "doctor_name": name,
            "normalized_name": identity.normalize_name(name),
            "provider_doctor_id": observed_id,
            "oladoc_profile_url": self.page.url,
        }
        result = identity.verify_identity(doctor, observed)
        if not result["match"]:
            raise DoctorMismatch(
                "The booking provider is showing a different doctor than the one selected: "
                + "; ".join(result["reasons"])
            )
        return observed

    # The provider's own time-slot container. Kept sharp and clickable while
    # the rest of the page is dimmed, so the patient's eye lands on the choice
    # they still have to make.
    TIME_BOX_SELECTOR = "#doctor-time-slots"

    def select_slot(self, date: str, time_label: str) -> bool:
        """Pick the patient's date, then their time, on the provider's page.

        The date and the time are treated the same way: find the provider's own
        control for the value the patient chose and click it, so the page ends
        up showing exactly what MediGuide recorded.
        """
        chips = self.page.locator(".slot-date")
        for index in range(chips.count()):
            cls = chips.nth(index).get_attribute("class") or ""
            if date in cls:
                chips.nth(index).scroll_into_view_if_needed(timeout=8000)
                chips.nth(index).click(timeout=15000)
                break
        else:
            return False
        self.page.wait_for_timeout(1500)

        # Draw the eye to the time box before trying to pick a time, so if the
        # exact minute is gone the patient is already looking at the choice.
        self.focus_time_selection()

        wanted = _minutes_of_day(time_label)
        timings = self.page.locator(".timing")
        for index in range(timings.count()):
            text = (timings.nth(index).inner_text(timeout=1500) or "").strip()
            # Oladoc publishes "06:00 PM" while MediGuide may hold "18:00".
            # Compare the actual time, not how it happens to be written.
            if text.upper() == str(time_label).upper() or (
                wanted is not None and _minutes_of_day(text) == wanted
            ):
                timings.nth(index).scroll_into_view_if_needed(timeout=8000)
                timings.nth(index).click(timeout=15000)
                self.clear_focus()
                return True
        return False

    def focus_time_selection(self) -> bool:
        """Dim the provider page except its time picker.

        Purely visual: the overlay never swallows clicks, because the patient
        may still need to reach a cookie banner, a CAPTCHA or an OTP field on
        the same page. Blocking those to make a point about focus would break
        the booking.
        """
        if self.page is None:
            return False
        try:
            return bool(self.page.evaluate(
                """(sel) => {
                    if (document.getElementById('mediguide-focus')) return true;
                    const box = document.querySelector(sel);
                    if (!box) return false;

                    const veil = document.createElement('div');
                    veil.id = 'mediguide-focus';
                    veil.style.cssText = [
                      'position:fixed', 'inset:0', 'z-index:2147483000',
                      'pointer-events:none',
                      'backdrop-filter:blur(2.5px) sepia(0.55) contrast(0.9) brightness(0.92)',
                      '-webkit-backdrop-filter:blur(2.5px) sepia(0.55) contrast(0.9) brightness(0.92)',
                      'background:rgba(58,42,24,0.18)'
                    ].join(';');
                    document.body.appendChild(veil);

                    // Lift the time picker above the veil so it stays sharp and
                    // clickable, and remember what to restore.
                    box.dataset.mediguidePrev = JSON.stringify({
                      position: box.style.position, zIndex: box.style.zIndex,
                      boxShadow: box.style.boxShadow, borderRadius: box.style.borderRadius,
                      background: box.style.background
                    });
                    box.style.position = 'relative';
                    box.style.zIndex = '2147483001';
                    box.style.background = '#fff';
                    box.style.borderRadius = '14px';
                    box.style.boxShadow = '0 0 0 3px rgba(15,122,99,.55), 0 18px 50px rgba(0,0,0,.28)';
                    box.scrollIntoView({block: 'center'});
                    return true;
                }""",
                self.TIME_BOX_SELECTOR,
            ))
        except Exception:
            logger.debug("Could not apply the time-focus effect", exc_info=True)
            return False

    def clear_focus(self) -> None:
        """Undo focus_time_selection once the time is settled."""
        if self.page is None:
            return
        try:
            self.page.evaluate(
                """(sel) => {
                    const veil = document.getElementById('mediguide-focus');
                    if (veil) veil.remove();
                    const box = document.querySelector(sel);
                    if (box && box.dataset.mediguidePrev) {
                        const prev = JSON.parse(box.dataset.mediguidePrev);
                        Object.assign(box.style, prev);
                        delete box.dataset.mediguidePrev;
                    }
                }""",
                self.TIME_BOX_SELECTOR,
            )
        except Exception:
            logger.debug("Could not clear the time-focus effect", exc_info=True)

    # Only visible inputs are candidates, and each attempt is short. A real
    # provider page carries many hidden inputs that match these names; waiting
    # the default timeout on each one would stall the whole booking.
    PATIENT_FIELDS = {
        "name": ["input[name*='name' i]:visible", "input[id*='name' i]:visible", "#patient_name:visible"],
        "email": ["input[type='email']:visible", "input[name*='email' i]:visible"],
        "phone": ["input[type='tel']:visible", "input[name*='phone' i]:visible",
                  "input[name*='mobile' i]:visible", "input[inputmode='tel']:visible"],
    }
    FILL_TIMEOUT_MS = 4000

    def _fill(self, selectors, value: str) -> bool:
        for selector in selectors:
            try:
                locator = self.page.locator(selector).first
                if locator.count() == 0:
                    continue
                locator.fill(str(value), timeout=self.FILL_TIMEOUT_MS)
                return True
            except Exception:
                continue
        return False

    def fill_patient(self, patient: Dict[str, Any]) -> Dict[str, Any]:
        """Fill everything the provider legitimately needs from the account.

        OTP and verification inputs are never touched - they are not in the map.
        """
        values = {
            "name": patient.get("fullName", ""),
            "email": patient.get("email", ""),
            "phone": patient.get("phone", ""),
        }
        return {
            field: self._fill(self.PATIENT_FIELDS[field], value)
            for field, value in values.items() if value
        }

    def otp_required(self) -> bool:
        try:
            body = (self.page.locator("body").inner_text(timeout=8000) or "").lower()
        except Exception:
            return False
        return any(m in body for m in ("otp", "one-time password", "verification code", "verify your number"))

    def submit_otp(self, code: str) -> bool:
        """Type the patient's own code into the provider's field. Never stored."""
        return self._fill(
            ["input[autocomplete='one-time-code']:visible", "input[name*='otp' i]:visible",
             "input[id*='otp' i]:visible", "input[name*='code' i]:visible",
             "input[inputmode='numeric']:visible"],
            code,
        )

    def submit_booking(self) -> Dict[str, Any]:
        if not submit_enabled():
            raise SubmitDisabled(
                "Final submission to the provider is disabled. Set OLADOC_SUBMIT_MODE=live to enable it."
            )
        from .playwright_booking import _click_first_matching

        if not _click_first_matching(self.page, [
            "button:has-text('Confirm Booking')", "button:has-text('Confirm')",
            "button:has-text('Book Appointment')", "button:has-text('Complete Booking')",
        ]):
            raise RuntimeError("The provider did not present a final confirmation control.")
        self.page.wait_for_timeout(4000)
        body = (self.page.locator("body").inner_text(timeout=10000) or "")
        confirmed = any(m in body.lower() for m in ("confirmed", "booking successful", "thank you"))
        reference = ""
        match = re.search(r"\b(?:booking|appointment|reference)[^A-Za-z0-9]{0,12}([A-Z0-9-]{5,20})\b", body, re.I)
        if match:
            reference = match.group(1)
        return {"confirmed": confirmed, "providerAppointmentId": reference, "url": self.page.url}

    def close(self) -> None:
        if self._stack is not None:
            try:
                self._stack.close()
            finally:
                self._stack, self.page = None, None


class _Worker:
    def __init__(self, session_id: str, driver: BookingDriver):
        self.session_id = session_id
        self.driver = driver
        self.otp_event = threading.Event()
        self._otp_code: Optional[str] = None
        self.thread: Optional[threading.Thread] = None

    def provide_otp(self, code: str) -> None:
        self._otp_code = str(code)
        self.otp_event.set()

    def take_otp(self) -> Optional[str]:
        """Read the code once and immediately forget it."""
        code, self._otp_code = self._otp_code, None
        return code


_workers: Dict[str, _Worker] = {}
_workers_lock = threading.Lock()


def _run(worker: _Worker, doctor: Dict[str, Any], date: str, time_label: str, patient: Dict[str, Any],
         on_confirmed: Optional[Callable[[Dict[str, Any]], None]] = None) -> None:
    session_id = worker.session_id
    try:
        # Resolving the clinic needs a live page load, so it happens here rather
        # than in the request that started the booking.
        if getattr(worker.driver, "needs_clinic", False):
            from . import oladoc_provider

            clinics = oladoc_provider.get_doctor_clinics(doctor)
            physical = [c for c in clinics if not c.get("isVideo")] or clinics
            if not physical:
                bs.registry.fail(session_id, "NO_CLINIC_PUBLISHED")
                return
            chosen = physical[0]
            worker.driver.booking_url = chosen["bookingUrl"]
            bs.registry.update(session_id, clinicId=chosen["clinicId"], clinicName=chosen["name"])
            checklist = dict(bs.registry.get(session_id).get("checklist") or {})
            if not checklist.get("clinicName"):
                checklist["clinicName"] = chosen["name"]
                bs.registry.update(session_id, checklist=checklist)

        worker.driver.open_and_verify(doctor)
        bs.registry.transition(session_id, bs.DOCTOR_VERIFIED)

        if not worker.driver.select_slot(date, time_label):
            # The date and time are still known to MediGuide, so the patient can
            # complete the booking manually rather than losing the flow.
            bs.registry.update(session_id, autofill={"date": False, "time": False}, autofillAttempted=True)
            bs.registry.fail(session_id, "SLOT_NO_LONGER_AVAILABLE")
            return
        bs.registry.transition(session_id, bs.SLOT_SELECTED)

        # Record what the provider page accepted so the checklist can ask the
        # patient only for what genuinely could not be filled automatically.
        try:
            filled = worker.driver.fill_patient(patient) or {}
        except Exception as exc:
            logger.warning("Autofill failed on the provider page: %s", exc)
            filled = {}
        bs.registry.update(session_id, autofill=dict(filled), autofillAttempted=True)
        flow_log.event(
            "PROVIDER_AUTOFILL",
            booking_session_id=session_id,
            filled=[k for k, v in filled.items() if v],
            not_filled=[k for k, v in filled.items() if not v],
        )
        bs.registry.transition(session_id, bs.PATIENT_DETAILS_FILLED)

        if worker.driver.otp_required():
            bs.registry.transition(session_id, bs.OTP_REQUIRED)
            flow_log.event("OTP_REQUIRED", booking_session_id=session_id)
            if not worker.otp_event.wait(timeout=OTP_WAIT_SECONDS):
                bs.registry.fail(session_id, "OTP_TIMEOUT")
                return
            code = worker.take_otp()
            if not code or not worker.driver.submit_otp(code):
                bs.registry.fail(session_id, "OTP_REJECTED")
                return
            del code  # not retained anywhere
            bs.registry.transition(session_id, bs.OTP_VERIFIED)
            flow_log.event("OTP_ACCEPTED", booking_session_id=session_id)

        bs.registry.transition(session_id, bs.BOOKING_SUBMITTED)
        outcome = worker.driver.submit_booking()
        if not outcome.get("confirmed"):
            bs.registry.fail(session_id, "PROVIDER_DID_NOT_CONFIRM")
            return

        session = bs.registry.transition(
            session_id, bs.CONFIRMED,
            providerAppointmentId=str(outcome.get("providerAppointmentId", "")),
        )
        if on_confirmed:
            on_confirmed(session)
    except DoctorMismatch as exc:
        logger.warning("Booking stopped on identity mismatch: %s", exc)
        bs.registry.fail(session_id, "DOCTOR_IDENTITY_MISMATCH")
    except SubmitDisabled as exc:
        logger.info("Booking reached submit with submission disabled: %s", exc)
        bs.registry.fail(session_id, "SUBMIT_DISABLED")
    except Exception as exc:
        logger.warning("Booking session %s failed: %s", session_id, exc, exc_info=True)
        bs.registry.fail(session_id, f"PROVIDER_ERROR:{type(exc).__name__}")
    finally:
        # The provider window is the patient's to finish in. Closing it the
        # moment automation stops is what made the browser appear to flash open
        # and vanish: the run usually ends before the booking does, because the
        # patient still has to pick a time, answer a CAPTCHA or enter an OTP.
        #
        # So the window stays open on the doctor's own Oladoc page unless
        # keeping it open would be wrong - a mismatched doctor, or a booking
        # the provider already confirmed.
        session_now = bs.registry.get(session_id) or {}
        state = session_now.get("state", "")
        error = str(session_now.get("error", ""))
        close_now = (
            state == bs.CONFIRMED
            or error == "DOCTOR_IDENTITY_MISMATCH"
            or error.startswith("PROVIDER_ERROR:")
            or error == "SESSION_EXPIRED"
        )
        if close_now:
            try:
                worker.driver.close()
            except Exception:
                logger.debug("Driver close failed", exc_info=True)
            with _workers_lock:
                _workers.pop(session_id, None)
        else:
            bs.registry.update(session_id, providerWindowOpen=True)
            flow_log.event("PROVIDER_WINDOW_LEFT_OPEN", booking_session_id=session_id, state=state)
            _schedule_window_close(session_id)


PATIENT_WINDOW_SECONDS = int(os.getenv("OLADOC_PATIENT_WINDOW_SECONDS", "900") or 900)


def _schedule_window_close(session_id: str) -> None:
    """Close a hand-off window eventually, so browsers cannot leak.

    The patient gets a long but bounded time to finish on Oladoc. Answering the
    "did it get booked?" question closes it sooner, via close_window().
    """
    def reap() -> None:
        with _workers_lock:
            worker = _workers.get(session_id)
        if worker is None:
            return
        try:
            worker.driver.close()
        except Exception:
            logger.debug("Timed window close failed", exc_info=True)
        with _workers_lock:
            _workers.pop(session_id, None)
        bs.registry.update(session_id, providerWindowOpen=False)
        flow_log.event("PROVIDER_WINDOW_TIMED_OUT", booking_session_id=session_id)

    timer = threading.Timer(PATIENT_WINDOW_SECONDS, reap)
    timer.daemon = True
    timer.start()


def close_window(session_id: str) -> bool:
    """Close the provider window for a session, if one is still open.

    Called when the patient tells us what happened: at that point they are done
    with the provider's page.
    """
    with _workers_lock:
        worker = _workers.get(session_id)
    if worker is None:
        return False
    try:
        worker.driver.close()
    except Exception:
        logger.debug("Driver close failed", exc_info=True)
    with _workers_lock:
        _workers.pop(session_id, None)
    bs.registry.update(session_id, providerWindowOpen=False)
    flow_log.event("PROVIDER_WINDOW_CLOSED", booking_session_id=session_id)
    return True


def start_booking(
    *,
    session: Dict[str, Any],
    doctor: Dict[str, Any],
    patient: Dict[str, Any],
    booking_url: str,
    driver: Optional[BookingDriver] = None,
    on_confirmed: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> None:
    """Begin the provider flow for an existing booking session."""
    session_id = session["bookingSessionId"]
    worker = _Worker(session_id, driver or OladocDriver(booking_url))
    with _workers_lock:
        _workers[session_id] = worker
    thread = threading.Thread(
        target=_run,
        args=(worker, doctor, session["date"], session["slotLabel"], patient, on_confirmed),
        name=f"booking-{session_id}",
        daemon=True,
    )
    worker.thread = thread
    thread.start()


def provide_otp(session_id: str, code: str) -> bool:
    """Hand the patient's code to the waiting worker."""
    with _workers_lock:
        worker = _workers.get(session_id)
    if worker is None:
        return False
    worker.provide_otp(code)
    return True


def is_running(session_id: str) -> bool:
    with _workers_lock:
        worker = _workers.get(session_id)
    return bool(worker and worker.thread and worker.thread.is_alive())
