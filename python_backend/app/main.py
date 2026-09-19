from __future__ import annotations

import logging
import os
from datetime import datetime
from time import monotonic
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse, RedirectResponse

from .auth import auth_response, hash_password, public_patient, user_from_token, verify_password
from .agents.appointment import validate_appointment
from .agents.chat_history import load_chat_context, save_chat_turn
from .agents.medical_information import decrypt_details, encrypt_details
from .agents.notifications import apply_delivery_status, notify_appointment, notify_cancellation
from .agents.payments import create_payment_intent
from .agents.qa import answer_symptoms, assess_structured
from .availability import BOOKING_WINDOW_DAYS, is_slot_available, list_slots
from .booking import build_official_booking_link
from .calcom import calcom_status, create_calcom_booking
from .config import ALLOWED_ORIGIN, APP_BASE_URL, PORT
from .db import db
from .models import SPECIALISTS, specialist_public
from .playwright_booking import (
    DoctorIdentityMismatch,
    DoctorUnavailable,
    book_with_canonical_doctor,
    search_live_oladoc_doctors,
)
from . import doctor_identity as identity
from . import flow_log
from .errors import register_error_handlers
from . import symptom_intake
from .rag import assess
from .ui import register_ui_routes

flow_log.configure()

logger = logging.getLogger(__name__)

app = FastAPI(title="MediGuide Python Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if ALLOWED_ORIGIN == "*" else [ALLOWED_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_error_handlers(app)
register_ui_routes(app)


@app.on_event("startup")
async def _warm_oladoc_directory() -> None:
    """Load the Oladoc directory in the background at startup.

    Building it means loading one Oladoc listing per specialty, which takes
    long enough that the first visitor would otherwise sit and wait. Warming it
    here means they get the cached copy. Failure is not fatal: the page reports
    an unreachable provider on its own.
    """
    import asyncio

    from . import oladoc_provider

    async def warm() -> None:
        try:
            doctors = await asyncio.to_thread(oladoc_provider.directory_doctors)
            logger.info("Oladoc directory warmed: %d doctors", len(doctors))
        except Exception as exc:
            logger.warning("Oladoc directory warm-up failed (%s); "
                           "the browse page will retry on demand.", type(exc).__name__)

    asyncio.create_task(warm())

_RATE_LIMIT: Dict[str, list[float]] = {}


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    content_length = int(request.headers.get("content-length", "0") or 0)
    if content_length > 1_000_000:
        return JSONResponse({"detail": "Request body is too large."}, status_code=413)
    client = request.client.host if request.client else "unknown"
    now = monotonic()
    recent = [stamp for stamp in _RATE_LIMIT.get(client, []) if now - stamp < 60]
    if request.method in {"POST", "PATCH", "PUT", "DELETE"} and len(recent) >= 120:
        return JSONResponse({"detail": "Too many requests. Try again shortly."}, status_code=429)
    recent.append(now)
    _RATE_LIMIT[client] = recent
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    # The UI is server-rendered with inline <style>/<script> blocks, so those are
    # allowed; everything else is same-origin only and framing is denied.
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; script-src 'self' 'unsafe-inline'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


def require_user(auth: Optional[str]) -> Dict[str, Any]:
    if not auth:
        raise HTTPException(status_code=401, detail="Please sign in to continue.")
    token = auth.replace("Bearer ", "", 1).strip()
    user = user_from_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Your session has expired. Please sign in again.")
    return user


class RegisterRequest(BaseModel):
    fullName: str
    email: str
    password: str
    phone: Optional[str] = None
    city: Optional[str] = None
    preferredContact: Optional[str] = None


class LoginRequest(BaseModel):
    email: str
    password: str


class BookingRequest(BaseModel):
    specialistId: str
    date: str
    time: str
    reason: str
    providerId: str = "clinic"
    confirmed: bool = True


class ExternalConfirmedRequest(BaseModel):
    specialistId: str
    date: str
    time: str
    reason: Optional[str] = None
    bookingReference: str


class PlaywrightBookingRequest(BaseModel):
    date: str
    time: str
    reason: str
    doctorId: Optional[str] = None
    specialistId: Optional[str] = None


class SymptomRequest(BaseModel):
    symptoms: str = Field(min_length=3, max_length=4000)
    conversationId: Optional[str] = None
    # Answers keyed by field name, e.g. {"severity": "7"}. Mirrors the web form,
    # where each answer is attributed to the question that was asked.
    answers: Optional[Dict[str, str]] = None


class ProfileUpdateRequest(BaseModel):
    fullName: Optional[str] = None
    phone: Optional[str] = None
    city: Optional[str] = None
    preferredContact: Optional[str] = None
    profilePicture: Optional[str] = None


class MedicalInformationRequest(BaseModel):
    title: str
    details: str
    category: str = "general"


class ComplaintRequest(BaseModel):
    subject: str
    details: str
    appointmentId: Optional[str] = None
    category: str = "general"
    desiredResolution: Optional[str] = None


class NotificationWebhook(BaseModel):
    notificationId: str
    status: str


class PaymentIntentRequest(BaseModel):
    purpose: str = "booking_facilitation_fee"
    currency: str = "pkr"


class AvailabilityRequest(BaseModel):
    date: str


class DoctorSearchRequest(BaseModel):
    symptoms: str = Field(min_length=3, max_length=4000)
    city: str = "Lahore"
    specialty: str = ""


class CalComBookingRequest(BaseModel):
    specialistId: str
    start: str = Field(min_length=10, max_length=80)
    timeZone: str = "Asia/Karachi"
    reason: str = Field(min_length=3, max_length=4000)


class ChecklistUpdateRequest(BaseModel):
    items: List[Dict[str, Any]]


def _checklist_items(reason: str) -> List[Dict[str, Any]]:
    guidance = assess(reason)
    return [
        {"id": "documents", "label": "Bring your accepted identification, current medicines, allergies, and relevant reports.", "completed": False},
        *[{"id": f"precaution-{index}", "label": precaution, "completed": False} for index, precaution in enumerate(guidance["precautions"], 1)],
        {"id": "arrival", "label": "Arrive 15 minutes before the appointment time.", "completed": False},
        {"id": "payment", "label": "Payment is pay at clinic; confirm the accepted method with the provider.", "completed": False},
    ]


def _create_checklist(user_id: str, appointment: Dict[str, Any]) -> Dict[str, Any]:
    return db.create_checklist(user_id, appointment["id"], _checklist_items(appointment.get("reason", "")))


@app.get("/")
def healthcheck() -> Dict[str, Any]:
    """Liveness plus a real database round trip."""
    database = db.health()
    return {
        "status": "ok" if database.get("status") == "ok" else "degraded",
        "service": "MediGuide Python backend",
        "database": database,
    }


@app.post("/auth/register")
def register(payload: RegisterRequest):
    email = payload.email.strip().lower()
    if not payload.fullName or not email or not payload.password:
        raise HTTPException(status_code=400, detail="Name, email, and password are required.")
    if "@" not in email or "." not in email:
        raise HTTPException(status_code=400, detail="Enter a valid email address.")
    if len(payload.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
    if db.find_user_by_email(email):
        raise HTTPException(status_code=409, detail="An account with that email already exists.")
    user = db.create_user({
        "fullName": payload.fullName,
        "email": email,
        "passwordHash": hash_password(payload.password),
        "phone": payload.phone or "",
        "city": payload.city or "Lahore",
        "preferredContact": payload.preferredContact or "both",
    })
    return auth_response(user)


@app.post("/auth/login")
def login(payload: LoginRequest):
    user = db.find_user_by_email(payload.email.strip().lower())
    if not user or not verify_password(payload.password, user["passwordHash"]):
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")
    return auth_response(user)


@app.get("/specialists")
def list_specialists():
    return {"specialists": [specialist_public(s) for s in SPECIALISTS], "directories": []}


@app.post("/doctors/search")
def search_doctors(payload: DoctorSearchRequest):
    """Search the live Oladoc directory using symptoms or a patient-provided care area."""
    try:
        doctors = search_live_oladoc_doctors(payload.symptoms, payload.city, payload.specialty)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if doctors:
        db.upsert_doctors([doctor for doctor in doctors if doctor.get("doctor_id")])
        return {"status": "found", "message": f"We found {len(doctors)} doctor(s) matching your care needs.", "doctors": doctors}
    if not payload.specialty:
        return {"status": "clarify", "message": "Please explain more clearly: tell us where your symptoms are, when they started, and your city.", "doctors": []}
    return {"status": "not_found", "message": "No doctor found for that search. Try another specialty or explain your symptoms differently.", "doctors": []}


@app.get("/booking/calcom/status")
def booking_calcom_status():
    return {**calcom_status(), "redirectUrl": f"{APP_BASE_URL}/ui"}


@app.get("/notifications/email/status")
def email_notification_status():
    configured = all(os.getenv(name, "").strip() for name in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"))
    return {
        "configured": configured,
        "fromConfigured": bool(os.getenv("SMTP_FROM", "").strip()),
        "message": "SMTP email notifications are ready." if configured else "Set SMTP_HOST, SMTP_USER, and SMTP_PASSWORD before starting the server.",
    }


@app.post("/ai/health-chat")
def health_chat(payload: SymptomRequest, authorization: Optional[str] = Header(default=None, alias="Authorization")):
    """Structured symptom intake over JSON.

    Mirrors the web flow: free text builds the structured record, only the
    missing fields are asked for, and the RAG layer is reached only once the
    record is complete.
    """
    user = require_user(authorization)
    conversation_id = payload.conversationId or f"chat-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"

    stored = db.get_intake(user["id"], conversation_id)
    record = symptom_intake.normalize_record((stored or {}).get("record"))

    errors: List[str] = []
    for field, answer in (payload.answers or {}).items():
        error = symptom_intake.apply_answer(record, field, str(answer))
        if error:
            errors.append(error)

    outstanding = symptom_intake.missing_fields(record)
    record = symptom_intake.apply_free_text(record, payload.symptoms)

    # If exactly one question was outstanding and free text did not resolve it,
    # the reply can only be answering that question.
    remaining = symptom_intake.missing_fields(record)
    if len(outstanding) == 1 and remaining == outstanding and not payload.answers:
        error = symptom_intake.apply_answer(record, outstanding[0], payload.symptoms)
        if error:
            errors.append(error)

    errors.extend(symptom_intake.validate(record))
    db.save_intake(user["id"], conversation_id, record)

    questions = symptom_intake.pending_questions(record)
    if questions or errors:
        save_chat_turn(user["id"], conversation_id, payload.symptoms.strip(), {
            "recommendation": {"specialty": None, "specialistIds": []},
            "followUpQuestions": [item["question"] for item in questions],
            "emergency": False, "confident": False,
        })
        return {
            "status": "needs_more_information",
            "conversationId": conversation_id,
            "structuredRecord": {key: record[key] for key in symptom_intake.FIELD_KEYS},
            "missingFields": symptom_intake.missing_fields(record),
            "followUpQuestions": questions,
            "validationErrors": errors,
            "message": "I need a few more details before I can look anything up. Only the missing items are listed.",
        }

    try:
        result = assess_structured(record)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "INCOMPLETE_SYMPTOMS", "message": str(exc)}) from exc
    except Exception as exc:
        flow_log.event(flow_log.RAG_FAILED, error=type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail={"error": "RAG_UNAVAILABLE", "message": "The medical knowledge search is unavailable."},
        ) from exc

    save_chat_turn(user["id"], conversation_id, payload.symptoms.strip(), {
        "recommendation": {"specialty": result["recommendedSpecialty"], "specialistIds": result["specialistIds"]},
        "retrievedDocuments": result["retrievedDocuments"],
        "emergency": result["urgent"], "followUpQuestions": [], "confident": True,
    })
    return {
        "status": "complete",
        "conversationId": conversation_id,
        "structuredRecord": result["structuredRecord"],
        "normalizedQuery": result["normalizedQuery"],
        "assessment": result["assessment"],
        "possibleConditions": result["possibleConditions"],
        "supportingSymptoms": result["supportingSymptoms"],
        "precautions": result["precautions"],
        "redFlags": result["redFlags"],
        "urgent": result["urgent"],
        "recommendedSpecialty": result["recommendedSpecialty"],
        "sources": result["sources"],
        "contextualResponse": result.get("contextualResponse"),
        "message": (
            "Some symptoms may need urgent assessment. Contact local emergency services if you are in immediate danger."
            if result["urgent"] else
            "This is general health information for care routing, not a diagnosis."
        ),
    }


@app.get("/ai/health-chat/history")
def health_chat_history(authorization: Optional[str] = Header(default=None, alias="Authorization")):
    user = require_user(authorization)
    records = db.get_records("chat_sessions", user["id"])
    return {"conversations": records}


@app.get("/profile")
def get_profile(authorization: Optional[str] = Header(default=None, alias="Authorization")):
    return public_patient(require_user(authorization))


@app.patch("/profile")
def update_profile(payload: ProfileUpdateRequest, authorization: Optional[str] = Header(default=None, alias="Authorization")):
    user = db.update_user(require_user(authorization)["id"], payload.model_dump(exclude_none=True))
    return public_patient(user or {})


@app.get("/medical-information")
def get_medical_information(authorization: Optional[str] = Header(default=None, alias="Authorization")):
    records = db.get_records("medical_information", require_user(authorization)["id"])
    return {"medicalInformation": [{**record, "details": decrypt_details(record) if record.get("encryptedDetails") else record.get("details", "")} for record in records]}


@app.post("/medical-information")
def create_medical_information(payload: MedicalInformationRequest, authorization: Optional[str] = Header(default=None, alias="Authorization")):
    user = require_user(authorization)
    encrypted = encrypt_details(payload.details)
    return db.create_record("medical_information", user["id"], {"title": payload.title, "category": payload.category, **encrypted})


@app.get("/complaints")
def get_complaints(authorization: Optional[str] = Header(default=None, alias="Authorization")):
    return {"complaints": db.get_records("complaints", require_user(authorization)["id"])}


@app.post("/complaints")
def create_complaint(payload: ComplaintRequest, authorization: Optional[str] = Header(default=None, alias="Authorization")):
    user = require_user(authorization)
    return db.create_record("complaints", user["id"], {**payload.model_dump(), "status": "open"})


@app.get("/booking/oladoc/{specialist_id}")
def oladoc_booking_redirect(specialist_id: str):
    specialist = next((s for s in SPECIALISTS if s["id"] == specialist_id), None)
    if not specialist or not specialist.get("oladocUrl"):
        raise HTTPException(status_code=404, detail="No verified Oladoc directory is available for this specialist.")
    return RedirectResponse(url=specialist["oladocUrl"], status_code=307)


@app.get("/appointments")
def get_appointments(authorization: Optional[str] = Header(default=None, alias="Authorization")):
    user = require_user(authorization)
    return {"appointments": db.get_appointments_for_user(user["id"])}


@app.get("/availability/{specialist_id}")
def specialist_availability(specialist_id: str, date: str):
    if not any(item["id"] == specialist_id for item in SPECIALISTS):
        raise HTTPException(status_code=404, detail="Specialist not found.")
    return {"specialistId": specialist_id, "date": date, "bookingWindowDays": BOOKING_WINDOW_DAYS, "slots": list_slots(specialist_id, date)}


@app.post("/appointments/{appointment_id}/cancel")
def cancel_appointment(appointment_id: str, authorization: Optional[str] = Header(default=None, alias="Authorization")):
    user = require_user(authorization)
    appointment = db.find_appointment_for_user(appointment_id, user["id"])
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    if appointment.get("status") == "cancelled":
        return appointment
    if appointment.get("status") == "completed":
        raise HTTPException(status_code=409, detail="Completed appointments cannot be cancelled.")
    updated = db.update_appointment(appointment_id, user["id"], {"status": "cancelled", "cancelledAt": datetime.utcnow().isoformat() + "Z"})
    updated["delivery"] = notify_cancellation(user, updated)
    return updated


@app.post("/appointments")
def create_appointment(payload: BookingRequest, authorization: Optional[str] = Header(default=None, alias="Authorization")):
    user = require_user(authorization)
    if payload.providerId != "clinic":
        raise HTTPException(status_code=400, detail="Only physical clinic appointments are available here.")
    specialist = next((s for s in SPECIALISTS if s["id"] == payload.specialistId), None)
    if not specialist:
        raise HTTPException(status_code=404, detail="Specialist not found.")
    validation = validate_appointment(payload.date, payload.time, payload.reason, payload.confirmed)
    if not validation["valid"]:
        raise HTTPException(status_code=400, detail=" ".join(validation["errors"]))
    slot = is_slot_available(specialist["id"], payload.date, payload.time, user["id"])
    if not slot["available"]:
        raise HTTPException(status_code=409, detail=slot["reason"])
    appointment = db.create_appointment(user["id"], {
        "specialistId": specialist["id"],
        "specialistName": specialist["name"],
        "specialistSpecialty": specialist["specialty"],
        "date": payload.date,
        "time": payload.time,
        "reason": payload.reason.strip(),
        "bookingReference": f"PY-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
        "providerId": payload.providerId,
        "providerName": "Clinic booking portal via Python",
        "bookingType": "physical",
        "paymentMethod": "pay-at-clinic",
        "source": "Python backend booking flow",
    })
    appointment["checklist"] = _create_checklist(user["id"], appointment)
    appointment["delivery"] = notify_appointment(user, appointment)
    return appointment


@app.post("/appointments/calcom")
def create_calcom_appointment(payload: CalComBookingRequest, authorization: Optional[str] = Header(default=None, alias="Authorization")):
    user = require_user(authorization)
    specialist = next((s for s in SPECIALISTS if s["id"] == payload.specialistId), None)
    if not specialist:
        raise HTTPException(status_code=404, detail="Specialist not found.")
    try:
        booking = create_calcom_booking(
            name=user["fullName"],
            email=user["email"],
            start=payload.start,
            time_zone=payload.timeZone,
            metadata={"specialistId": specialist["id"], "reason": payload.reason.strip()},
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    appointment = db.create_appointment(user["id"], {
        "specialistId": specialist["id"],
        "specialistName": specialist["name"],
        "specialistSpecialty": specialist["specialty"],
        "date": payload.start[:10],
        "time": payload.start[11:16],
        "reason": payload.reason.strip(),
        "bookingReference": booking.get("data", booking).get("uid", f"CAL-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"),
        "providerId": "cal.com",
        "providerName": "Cal.com scheduling",
        "bookingType": "physical",
        "paymentMethod": "pay-at-clinic",
        "calComBooking": booking,
        "returnUrl": f"{APP_BASE_URL}/ui/confirmation/{{appointmentId}}",
    })
    appointment["checklist"] = _create_checklist(user["id"], appointment)
    appointment["delivery"] = notify_appointment(user, appointment)
    appointment["returnUrl"] = f"{APP_BASE_URL}/ui/confirmation/{appointment['id']}"
    return appointment


@app.get("/checklists/{appointment_id}")
def get_checklist(appointment_id: str, authorization: Optional[str] = Header(default=None, alias="Authorization")):
    user = require_user(authorization)
    appointment = db.find_appointment_for_user(appointment_id, user["id"])
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    checklist = db.find_checklist_by_appointment(appointment_id, user["id"]) or _create_checklist(user["id"], appointment)
    return {"appointment": appointment, "checklist": checklist}


@app.patch("/checklists/{checklist_id}")
def update_checklist(checklist_id: str, payload: ChecklistUpdateRequest, authorization: Optional[str] = Header(default=None, alias="Authorization")):
    user = require_user(authorization)
    if any("id" not in item or "label" not in item for item in payload.items):
        raise HTTPException(status_code=400, detail="Each checklist item needs an id and label.")
    checklist = db.update_checklist(checklist_id, user["id"], payload.items)
    if not checklist:
        raise HTTPException(status_code=404, detail="Checklist not found.")
    appointment = db.find_appointment_for_user(checklist["appointmentId"], user["id"])
    if checklist["completed"] and appointment and appointment.get("status") != "confirmed":
        db.update_appointment(appointment["id"], user["id"], {"status": "confirmed"})
    return checklist


@app.post("/appointments/external-confirmed")
def external_confirmed(payload: ExternalConfirmedRequest, authorization: Optional[str] = Header(default=None, alias="Authorization")):
    user = require_user(authorization)
    specialist = next((s for s in SPECIALISTS if s["id"] == payload.specialistId), None)
    if not specialist:
        raise HTTPException(status_code=404, detail="Specialist not found.")
    if not payload.bookingReference.strip():
        raise HTTPException(status_code=400, detail="Enter the booking reference number.")
    appointment = db.create_appointment(user["id"], {
        "specialistId": specialist["id"],
        "specialistName": specialist["name"],
        "specialistSpecialty": specialist["specialty"],
        "date": payload.date,
        "time": payload.time,
        "reason": (payload.reason or f"Appointment with {specialist['name']}").strip(),
        "bookingReference": payload.bookingReference.strip(),
        "providerId": "oladoc",
        "providerName": "Oladoc (patient-reported confirmation)",
        "bookingType": "physical",
        "paymentMethod": "pay-at-clinic",
        "status": "external-confirmed",
        "source": "Patient completed booking on Oladoc",
    })
    appointment["delivery"] = notify_appointment(user, appointment)
    return appointment


@app.get("/notifications")
def get_notifications(authorization: Optional[str] = Header(default=None, alias="Authorization")):
    return {"notifications": db.get_notifications_for_user(require_user(authorization)["id"])}


@app.post("/webhooks/whatsapp")
def whatsapp_delivery_webhook(payload: NotificationWebhook):
    record = apply_delivery_status(payload.notificationId, payload.status)
    if not record:
        raise HTTPException(status_code=404, detail="Notification or delivery status not found.")
    return record


@app.post("/payments/payment-intent")
def payment_intent(payload: PaymentIntentRequest, authorization: Optional[str] = Header(default=None, alias="Authorization")):
    user = require_user(authorization)
    try:
        intent = create_payment_intent(payload.purpose, payload.currency)
        record = db.create_payment(user["id"], {**intent, "status": "requires_confirmation"})
        return {**intent, "paymentRecordId": record["id"], "status": record["status"]}
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/payments")
def payment_history(authorization: Optional[str] = Header(default=None, alias="Authorization")):
    return {"payments": db.get_payments_for_user(require_user(authorization)["id"])}


@app.post("/booking/redirect-link")
def redirect_link(payload: Dict[str, Any], authorization: Optional[str] = Header(default=None, alias="Authorization")):
    user = require_user(authorization)
    specialist = next((s for s in SPECIALISTS if s["id"] == payload.get("specialistId")), None)
    if not specialist:
        raise HTTPException(status_code=404, detail="Specialist not found.")
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="Describe the reason for the visit before generating a link.")

    handoff = build_official_booking_link(
        specialist=specialist,
        reason=reason,
        preferred_date=str(payload.get("preferredDate") or ""),
        preferred_time=str(payload.get("preferredTime") or ""),
        patient_name=user["fullName"],
    )

    record = db.create_redirect_handoff(user["id"], {
        "specialistId": specialist["id"],
        "specialistName": specialist["name"],
        "reason": reason,
        "preferredDate": payload.get("preferredDate") or "",
        "preferredTime": payload.get("preferredTime") or "",
        "referenceId": handoff["referenceId"],
        "url": handoff["url"],
        "paymentId": payload.get("paymentId") or None,
        "summary": handoff["summary"],
    })
    return {**handoff, "handoffId": record["id"]}


@app.get("/booking/redirect-handoffs")
def redirect_handoffs(authorization: Optional[str] = Header(default=None, alias="Authorization")):
    user = require_user(authorization)
    return {"handoffs": db.get_redirect_handoffs_for_user(user["id"])}


@app.get("/booking/providers")
def booking_providers():
    return {
        "ready": True,
        "providers": [
            {
                "id": "clinic",
                "name": "Clinic portal via Python",
                "type": "physical-clinic-python",
                "description": "Playwright fills the appointment and patient details in a visible clinic window; the patient completes any OTP or CAPTCHA.",
                "supportsAutoBooking": True,
                "requiresManualVerification": True,
                "patientVerification": "otp-or-captcha-by-patient",
                "envKeys": [],
                "missingEnvKeys": [],
                "ready": True,
            },
            {
                "id": "oladoc",
                "name": "Oladoc official directory",
                "type": "official-provider-redirect",
                "description": "Opens Oladoc's verified specialty directory so patients can choose current doctors and live slots there.",
                "supportsAutoBooking": False,
                "requiresManualVerification": True,
                "envKeys": [],
                "missingEnvKeys": [],
                "ready": True,
            },
        ],
    }


@app.post("/appointments/confirm")
def confirm_appointment(payload: BookingRequest, authorization: Optional[str] = Header(default=None, alias="Authorization")):
    return create_appointment(payload, authorization)


def resolve_canonical_doctor(doctor_id: Optional[str], specialist_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Resolve a booking request to a canonical doctor, server-side.

    A client-supplied name is never used to find a doctor. Only the canonical
    id, or a known directory specialist id, resolves.
    """
    target = str(doctor_id or "").strip()
    if target:
        stored = db.find_doctor(target)
        if stored:
            return stored
    fallback = str(specialist_id or "").strip()
    for specialist in SPECIALISTS:
        try:
            canonical = identity.from_specialist(specialist)
        except identity.DoctorIdentityError:
            continue
        if canonical["doctor_id"] == target or (fallback and specialist["id"] == fallback):
            return db.upsert_doctor(canonical)
    return None


@app.post("/appointments/playwright")
def playwright_appointment(
    payload: PlaywrightBookingRequest,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
):
    """Book the exact selected doctor on Oladoc, verifying identity before submitting."""
    user = require_user(authorization)
    doctor = resolve_canonical_doctor(payload.doctorId, payload.specialistId)
    if not doctor:
        raise HTTPException(
            status_code=404,
            detail={"error": "DOCTOR_ID_MISSING", "message": "No doctor could be resolved from the supplied identity."},
        )
    validation = validate_appointment(payload.date, payload.time, payload.reason, True)
    if not validation["valid"]:
        raise HTTPException(status_code=400, detail=" ".join(validation["errors"]))

    try:
        result = book_with_canonical_doctor(
            doctor, payload.date, payload.time, payload.reason.strip(), user
        )
    except DoctorIdentityMismatch as exc:
        # 409: the provider is showing someone else. Never substitute.
        raise HTTPException(status_code=409, detail=exc.to_dict()) from exc
    except DoctorUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": DoctorUnavailable.code, "message": str(exc), "doctorId": doctor["doctor_id"]},
        ) from exc
    except RuntimeError as exc:
        flow_log.event(flow_log.BOOKING_FAILED, doctor_id=doctor["doctor_id"], error=type(exc).__name__)
        raise HTTPException(status_code=503, detail={"error": "BOOKING_FAILED", "message": str(exc)}) from exc

    appointment = db.create_appointment(user["id"], {
        "doctorId": doctor["doctor_id"],
        "specialistId": doctor.get("booking_metadata", {}).get("specialistId", ""),
        "specialistName": doctor["doctor_name"],
        "specialistSpecialty": doctor.get("specialty", ""),
        "providerDoctorId": doctor.get("provider_doctor_id", ""),
        "providerProfileUrl": doctor.get("oladoc_profile_url", ""),
        "date": payload.date,
        "time": payload.time,
        "reason": payload.reason.strip(),
        "bookingReference": result["bookingReference"],
        "providerId": result["providerId"],
        "providerName": result["providerName"],
        "bookingType": "physical",
        "paymentMethod": "pay-at-clinic",
        "status": result["status"],
        "source": result["bookingSource"],
    })
    appointment["delivery"] = notify_appointment(user, appointment)
    return appointment


@app.get("/doctors/{doctor_id:path}/canonical")
def canonical_doctor_record(doctor_id: str, authorization: Optional[str] = Header(default=None, alias="Authorization")):
    """The canonical identity the whole flow shares, for clients and debugging."""
    require_user(authorization)
    doctor = resolve_canonical_doctor(doctor_id, None)
    if not doctor:
        raise HTTPException(status_code=404, detail={"error": "DOCTOR_ID_MISSING", "message": "Unknown doctor id."})
    return doctor


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("python_backend.app.main:app", host="0.0.0.0", port=PORT, reload=False)
