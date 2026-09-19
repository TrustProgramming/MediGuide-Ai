"""One-off import of the legacy JSON store into the database.

Validates first, imports second, verifies third. Nothing is deleted here - the
JSON files are left untouched so the import can be re-run and compared.

Safe to run more than once: rows are matched on their original id, so a second
run updates rather than duplicates.

    python -m python_backend.app.database.migrate_json           # import + verify
    python -m python_backend.app.database.migrate_json --verify  # verify only
    python -m python_backend.app.database.migrate_json --dry-run # validate only
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from sqlalchemy import func, select

from ..config import DATA_DIR
from .engine import session_scope
from .models import (
    Appointment,
    Checklist,
    ChecklistItem,
    Complaint,
    Conversation,
    ConversationTurn,
    Doctor,
    MedicalInformation,
    Notification,
    Payment,
    RedirectHandoff,
    SymptomIntake,
    User,
)
from .repositories import _parse_dt, new_id

# JSON file -> what it holds. Files not listed here are not persistent app data.
SOURCES = {
    "users": "users",
    "appointments": "appointments",
    "chat-sessions": "conversation turns",
    "notifications-log": "notifications",
    "checklists": "checklists",
    "medical-information": "medical information",
    "complaints": "complaints",
    "payments": "payments",
    "redirect-handoffs": "redirect handoffs",
    "doctors": "doctors",
    "symptom-intakes": "symptom intakes",
}


def load(name: str) -> List[Dict[str, Any]]:
    path = DATA_DIR / f"{name}.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name} is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError(f"{path.name} does not contain a list")
    return [item for item in data if isinstance(item, dict)]


def validate() -> Dict[str, Any]:
    """Report problems before anything is written."""
    report: Dict[str, Any] = {"counts": {}, "problems": [], "warnings": []}
    users = load("users")
    report["counts"]["users"] = len(users)

    user_ids = set()
    emails = set()
    for user in users:
        if not user.get("id"):
            report["problems"].append("a user record has no id")
            continue
        if user["id"] in user_ids:
            report["problems"].append(f"duplicate user id {user['id']}")
        user_ids.add(user["id"])
        email = str(user.get("email", "")).strip().lower()
        if not email:
            report["problems"].append(f"user {user['id']} has no email")
        elif email in emails:
            report["problems"].append(f"duplicate email {email}")
        else:
            emails.add(email)
        if not user.get("passwordHash"):
            report["problems"].append(f"user {user['id']} has no password hash")

    for name in ("appointments", "chat-sessions", "notifications-log", "checklists",
                 "medical-information", "complaints", "payments", "redirect-handoffs",
                 "symptom-intakes"):
        rows = load(name)
        report["counts"][name] = len(rows)
        seen = set()
        for row in rows:
            row_id = row.get("id")
            if row_id in seen:
                report["problems"].append(f"{name}: duplicate id {row_id}")
            seen.add(row_id)
            owner = row.get("userId")
            if owner and owner not in user_ids:
                # Recorded, not silently dropped.
                report["warnings"].append(f"{name}: row {row_id} references unknown user {owner}")

    doctors = load("doctors")
    report["counts"]["doctors"] = len(doctors)
    for doctor in doctors:
        if not doctor.get("doctor_id"):
            report["problems"].append("a doctor record has no doctor_id")

    report["ok"] = not report["problems"]
    return report


def _dt(value: Any) -> datetime:
    return _parse_dt(value) or datetime.now(timezone.utc)


def migrate() -> Dict[str, Any]:
    """Import every JSON collection. Idempotent on original ids."""
    stats: Dict[str, int] = {}
    skipped: List[str] = []

    with session_scope() as session:
        # --- users ---------------------------------------------------------
        users = load("users")
        known_users = set()
        for item in users:
            row = session.get(User, item["id"]) or User(id=item["id"])
            row.full_name = str(item.get("fullName", ""))
            row.email = str(item.get("email", "")).strip().lower()
            row.password_hash = str(item.get("passwordHash", ""))
            row.phone = str(item.get("phone") or "")
            row.city = str(item.get("city") or "Lahore")
            contact = str(item.get("preferredContact") or "both")
            row.preferred_contact = contact if contact in {"email", "whatsapp", "phone", "both"} else "both"
            row.profile_picture = str(item.get("profilePicture") or "")
            row.created_at = _dt(item.get("createdAt"))
            session.add(row)
            known_users.add(item["id"])
        session.flush()
        stats["users"] = len(users)

        # --- doctors -------------------------------------------------------
        doctors = load("doctors")
        for item in doctors:
            row = session.get(Doctor, item["doctor_id"]) or Doctor(doctor_id=item["doctor_id"])
            row.doctor_name = str(item.get("doctor_name", ""))
            row.normalized_name = str(item.get("normalized_name", ""))
            row.specialty = str(item.get("specialty", ""))
            row.normalized_specialty = str(item.get("normalized_specialty", ""))
            row.location = str(item.get("location", ""))
            row.clinic = str(item.get("clinic", ""))
            row.source = str(item.get("source", "live_search"))
            row.provider = str(item.get("provider", "oladoc"))
            row.provider_doctor_id = str(item.get("provider_doctor_id", ""))
            row.oladoc_profile_url = str(item.get("oladoc_profile_url", ""))
            row.oladoc_directory_url = str(item.get("oladoc_directory_url", ""))
            row.rating_label = str(item.get("rating_label") or item.get("ratingLabel") or "")
            row.identity_strength = str(item.get("identity_strength", "name_specialty"))
            row.booking_metadata = dict(item.get("booking_metadata") or {})
            session.add(row)
        session.flush()
        stats["doctors"] = len(doctors)

        # --- appointments --------------------------------------------------
        known_columns = {
            "id", "userId", "createdAt", "updatedAt", "cancelledAt", "doctorId",
            "specialistId", "specialistName", "specialistSpecialty", "providerDoctorId",
            "providerProfileUrl", "clinicId", "clinicName", "clinicAddress", "clinicFee",
            "date", "time", "timeLabel", "timezone", "reason", "bookingType",
            "paymentMethod", "bookingStatus", "status", "bookingReference",
            "providerAppointmentId", "confirmationReference", "providerId", "providerName",
            "bookingSessionId", "slotId", "source", "emailStatus", "emailDetail",
            "doctorName", "provider",
        }
        appointments = load("appointments")
        valid_statuses = {"SELECTED", "IN_PROGRESS", "AWAITING_OTP", "CONFIRMED", "FAILED", "CANCELLED"}
        imported_appointments = set()
        for item in appointments:
            if item.get("userId") not in known_users:
                skipped.append(f"appointment {item.get('id')}: unknown user")
                continue
            row = session.get(Appointment, item["id"]) or Appointment(id=item["id"])
            row.user_id = item["userId"]
            doctor_id = str(item.get("doctorId") or "")
            row.doctor_id = doctor_id if doctor_id and session.get(Doctor, doctor_id) else None
            row.specialist_id = str(item.get("specialistId", ""))
            row.specialist_name = str(item.get("specialistName", ""))
            row.specialist_specialty = str(item.get("specialistSpecialty", ""))
            row.provider_doctor_id = str(item.get("providerDoctorId", ""))
            row.provider_profile_url = str(item.get("providerProfileUrl", ""))
            row.clinic_id = str(item.get("clinicId", ""))
            row.clinic_name = str(item.get("clinicName", ""))
            row.clinic_address = str(item.get("clinicAddress", ""))
            row.clinic_fee = str(item.get("clinicFee", ""))
            row.appointment_date = str(item.get("date", ""))
            row.appointment_time = str(item.get("time", ""))
            row.time_label = str(item.get("timeLabel") or item.get("time", ""))
            row.timezone_name = str(item.get("timezone") or "Asia/Karachi")
            row.reason = str(item.get("reason", ""))
            row.booking_type = str(item.get("bookingType") or "physical")
            row.payment_method = str(item.get("paymentMethod") or "pay-at-clinic")
            legacy_status = str(item.get("status", "confirmed"))
            booking_status = str(item.get("bookingStatus") or "").upper()
            if booking_status not in valid_statuses:
                # Legacy rows carried only the lowercase `status` field.
                booking_status = {
                    "confirmed": "CONFIRMED", "cancelled": "CANCELLED",
                    "failed": "FAILED", "completed": "CONFIRMED",
                }.get(legacy_status.lower(), "SELECTED")
            row.booking_status = booking_status
            row.status = legacy_status
            row.booking_reference = str(item.get("bookingReference", ""))
            row.provider_appointment_id = str(item.get("providerAppointmentId", ""))
            row.confirmation_reference = str(item.get("confirmationReference", ""))
            row.provider_id = str(item.get("providerId", ""))
            row.provider_name = str(item.get("providerName", ""))
            row.booking_session_id = str(item.get("bookingSessionId", ""))
            row.slot_id = str(item.get("slotId", ""))
            row.source = str(item.get("source", ""))
            email_status = str(item.get("emailStatus") or "PENDING").upper()
            row.email_status = email_status if email_status in {"PENDING", "SENT", "FAILED", "NOT_CONFIGURED"} else "PENDING"
            row.email_detail = str(item.get("emailDetail", ""))
            row.cancelled_at = _parse_dt(item.get("cancelledAt"))
            row.created_at = _dt(item.get("createdAt"))
            # Anything the legacy record carried that has no column is preserved.
            row.extra = {k: v for k, v in item.items() if k not in known_columns}
            session.add(row)
            imported_appointments.add(item["id"])
        session.flush()
        stats["appointments"] = len(imported_appointments)

        # --- conversations + turns ------------------------------------------
        # The JSON held one row per turn with the conversation id repeated.
        turns = load("chat-sessions")
        conversation_ids: Dict[Tuple[str, str], str] = {}
        ordered = sorted(turns, key=lambda t: str(t.get("createdAt", "")))
        imported_turns = 0
        for item in ordered:
            user_id = item.get("userId")
            if user_id not in known_users:
                skipped.append(f"chat turn {item.get('id')}: unknown user")
                continue
            key = str(item.get("conversationId") or f"legacy-{item.get('id')}")
            lookup = (user_id, key)
            conversation_id = conversation_ids.get(lookup)
            if conversation_id is None:
                existing = session.scalar(
                    select(Conversation).where(
                        Conversation.user_id == user_id, Conversation.conversation_key == key
                    )
                )
                if existing is None:
                    existing = Conversation(
                        id=new_id(12), user_id=user_id, conversation_key=key,
                        created_at=_dt(item.get("createdAt")),
                    )
                    session.add(existing)
                    session.flush()
                conversation_id = existing.id
                conversation_ids[lookup] = conversation_id

            turn = session.get(ConversationTurn, item["id"]) or ConversationTurn(id=item["id"])
            turn.conversation_id = conversation_id
            turn.position = int(session.scalar(
                select(func.count()).select_from(ConversationTurn)
                .where(ConversationTurn.conversation_id == conversation_id)
            ) or 0) if turn.position is None else turn.position
            existing_count = session.scalar(
                select(func.count()).select_from(ConversationTurn)
                .where(ConversationTurn.conversation_id == conversation_id)
            ) or 0
            turn.position = int(existing_count)
            turn.symptoms = str(item.get("symptoms", ""))
            turn.internal_assessment = str(item.get("internalAssessment") or "")
            turn.disease_name = str(item.get("diseaseName") or "")
            turn.emergency = bool(item.get("emergency", False))
            turn.recommendation = item.get("recommendation") or {}
            turn.retrieved_documents = item.get("retrievedDocuments") or []
            turn.follow_up_questions = item.get("followUpQuestions") or []
            turn.question_answers = item.get("questionAnswers") or []
            turn.created_at = _dt(item.get("createdAt"))
            session.add(turn)
            session.flush()
            imported_turns += 1
        stats["conversation_turns"] = imported_turns
        stats["conversations"] = len(conversation_ids)

        # --- symptom intakes -------------------------------------------------
        intakes = load("symptom-intakes")
        imported_intakes = 0
        for item in intakes:
            user_id = item.get("userId")
            if user_id not in known_users:
                skipped.append(f"intake {item.get('id')}: unknown user")
                continue
            key = str(item.get("conversationId", ""))
            conversation = session.scalar(
                select(Conversation).where(
                    Conversation.user_id == user_id, Conversation.conversation_key == key
                )
            )
            if conversation is None:
                conversation = Conversation(id=new_id(12), user_id=user_id, conversation_key=key)
                session.add(conversation)
                session.flush()
            row = session.get(SymptomIntake, item.get("id") or "") or SymptomIntake(id=item.get("id") or new_id(10))
            row.user_id = user_id
            row.conversation_id = conversation.id
            row.record = item.get("record") or {}
            session.add(row)
            imported_intakes += 1
        session.flush()
        stats["symptom_intakes"] = imported_intakes

        # --- notifications ---------------------------------------------------
        notifications = load("notifications-log")
        imported_notifications = 0
        for item in notifications:
            if item.get("userId") not in known_users:
                skipped.append(f"notification {item.get('id')}: unknown user")
                continue
            row = session.get(Notification, item["id"]) or Notification(id=item["id"])
            row.user_id = item["userId"]
            appointment_id = str(item.get("appointmentId") or "")
            row.appointment_id = appointment_id if appointment_id in imported_appointments else None
            channel = str(item.get("channel", "email"))
            row.channel = channel if channel in {"email", "whatsapp", "sms"} else "email"
            row.status = str(item.get("status", "pending"))
            row.recipient = str(item.get("recipient", ""))
            row.detail = str(item.get("detail", ""))
            row.message = str(item.get("message", ""))
            row.created_at = _dt(item.get("createdAt"))
            session.add(row)
            imported_notifications += 1
        session.flush()
        stats["notifications"] = imported_notifications

        # --- checklists + items ----------------------------------------------
        checklists = load("checklists")
        imported_checklists = 0
        imported_items = 0
        for item in checklists:
            if item.get("userId") not in known_users or item.get("appointmentId") not in imported_appointments:
                skipped.append(f"checklist {item.get('id')}: unknown user or appointment")
                continue
            row = session.get(Checklist, item["id"]) or Checklist(id=item["id"])
            row.user_id = item["userId"]
            row.appointment_id = item["appointmentId"]
            row.completed = bool(item.get("completed", False))
            row.created_at = _dt(item.get("createdAt"))
            row.items.clear()
            for position, entry in enumerate(item.get("items") or []):
                row.items.append(ChecklistItem(
                    item_key=str(entry.get("id", f"item-{position}")),
                    label=str(entry.get("label", "")),
                    completed=bool(entry.get("completed", False)),
                    position=position,
                ))
                imported_items += 1
            session.add(row)
            imported_checklists += 1
        session.flush()
        stats["checklists"] = imported_checklists
        stats["checklist_items"] = imported_items

        # --- simple user-owned collections -----------------------------------
        def simple(name: str, model, build) -> int:
            rows = load(name)
            count = 0
            for entry in rows:
                if entry.get("userId") not in known_users:
                    skipped.append(f"{name}: row {entry.get('id')} has unknown user")
                    continue
                row = session.get(model, entry.get("id") or "") or model(id=entry.get("id") or new_id(10))
                build(row, entry)
                row.user_id = entry["userId"]
                row.created_at = _dt(entry.get("createdAt"))
                session.add(row)
                count += 1
            session.flush()
            return count

        def build_medical(row, entry):
            row.title = str(entry.get("title", ""))
            row.category = str(entry.get("category", "general"))
            row.encrypted_details = str(entry.get("encryptedDetails", ""))
            row.nonce = str(entry.get("nonce", ""))
            row.encryption = str(entry.get("encryption", "AES-256-GCM"))

        def build_complaint(row, entry):
            row.subject = str(entry.get("subject", ""))
            row.details = str(entry.get("details", ""))
            row.category = str(entry.get("category", "general"))
            row.desired_resolution = str(entry.get("desiredResolution") or "")
            row.status = str(entry.get("status", "open"))
            appointment_id = str(entry.get("appointmentId") or "")
            row.appointment_id = appointment_id if appointment_id in imported_appointments else None

        def build_payment(row, entry):
            row.purpose = str(entry.get("purpose", ""))
            row.amount = int(entry.get("amount") or 0)
            row.currency = str(entry.get("currency", "pkr"))
            row.status = str(entry.get("status", "requires_confirmation"))
            row.provider_intent_id = str(entry.get("providerIntentId") or "")
            row.mode = str(entry.get("mode", "test"))

        def build_handoff(row, entry):
            row.specialist_id = str(entry.get("specialistId", ""))
            row.specialist_name = str(entry.get("specialistName", ""))
            row.reason = str(entry.get("reason", ""))
            row.preferred_date = str(entry.get("preferredDate") or "")
            row.preferred_time = str(entry.get("preferredTime") or "")
            row.reference_id = str(entry.get("referenceId", ""))
            row.url = str(entry.get("url", ""))
            row.summary = str(entry.get("summary", ""))
            row.payment_id = entry.get("paymentId") or None

        stats["medical_information"] = simple("medical-information", MedicalInformation, build_medical)
        stats["complaints"] = simple("complaints", Complaint, build_complaint)
        stats["payments"] = simple("payments", Payment, build_payment)
        stats["redirect_handoffs"] = simple("redirect-handoffs", RedirectHandoff, build_handoff)

    return {"imported": stats, "skipped": skipped}


def verify() -> Dict[str, Any]:
    """Compare the JSON source against the database, row for row."""
    checks: List[Dict[str, Any]] = []

    def compare(label: str, source_count: int, db_count: int, note: str = "") -> None:
        checks.append({
            "collection": label, "source": source_count, "database": db_count,
            "match": source_count == db_count, "note": note,
        })

    with session_scope() as session:
        count = lambda model: int(session.scalar(select(func.count()).select_from(model)) or 0)

        users = load("users")
        compare("users", len(users), count(User))
        compare("appointments", len(load("appointments")), count(Appointment))
        compare("conversation turns", len(load("chat-sessions")), count(ConversationTurn))
        compare("notifications", len(load("notifications-log")), count(Notification))
        compare("checklists", len(load("checklists")), count(Checklist))
        compare("medical information", len(load("medical-information")), count(MedicalInformation))
        compare("complaints", len(load("complaints")), count(Complaint))
        compare("payments", len(load("payments")), count(Payment))
        compare("redirect handoffs", len(load("redirect-handoffs")), count(RedirectHandoff))
        compare("doctors", len(load("doctors")), count(Doctor))

        # Derivation rule: one conversation per distinct (user, conversationId).
        # A turn that carries no conversationId is not demonstrably part of any
        # other turn's conversation, so it gets its own rather than being merged
        # with unrelated turns.
        chat_turns = load("chat-sessions")
        keyed = {
            (t.get("userId"), t.get("conversationId"))
            for t in chat_turns if t.get("userId") and t.get("conversationId")
        }
        unkeyed = sum(1 for t in chat_turns if t.get("userId") and not t.get("conversationId"))
        expected_conversations = len(keyed) + unkeyed
        compare("conversations (derived)", expected_conversations, count(Conversation),
                f"{len(keyed)} keyed + {unkeyed} turn(s) with no conversationId")

        # Field-level spot check, not just counts.
        field_problems: List[str] = []
        for item in users:
            row = session.get(User, item["id"])
            if row is None:
                field_problems.append(f"user {item['id']} missing")
                continue
            if row.email != str(item.get("email", "")).strip().lower():
                field_problems.append(f"user {item['id']} email differs")
            if row.password_hash != item.get("passwordHash"):
                field_problems.append(f"user {item['id']} password hash differs")
            if row.full_name != str(item.get("fullName", "")):
                field_problems.append(f"user {item['id']} name differs")

        for item in load("appointments"):
            row = session.get(Appointment, item["id"])
            if row is None:
                field_problems.append(f"appointment {item['id']} missing")
                continue
            if row.appointment_date != str(item.get("date", "")):
                field_problems.append(f"appointment {item['id']} date differs")
            if row.specialist_name != str(item.get("specialistName", "")):
                field_problems.append(f"appointment {item['id']} doctor name differs")

        for item in load("chat-sessions"):
            row = session.get(ConversationTurn, item["id"])
            if row is None:
                field_problems.append(f"chat turn {item['id']} missing")
            elif row.symptoms != str(item.get("symptoms", "")):
                field_problems.append(f"chat turn {item['id']} symptoms differ")

        orphans = {
            "appointments without a user": int(session.scalar(
                select(func.count()).select_from(Appointment)
                .where(~Appointment.user_id.in_(select(User.id)))) or 0),
            "turns without a conversation": int(session.scalar(
                select(func.count()).select_from(ConversationTurn)
                .where(~ConversationTurn.conversation_id.in_(select(Conversation.id)))) or 0),
        }

    return {
        "checks": checks,
        "all_counts_match": all(c["match"] for c in checks),
        "field_problems": field_problems,
        "orphans": orphans,
        "ok": all(c["match"] for c in checks) and not field_problems and not any(orphans.values()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Import the legacy JSON store into the database")
    parser.add_argument("--verify", action="store_true", help="only compare JSON against the database")
    parser.add_argument("--dry-run", action="store_true", help="only validate the JSON")
    args = parser.parse_args()

    validation = validate()
    print("== validation ==")
    print(json.dumps(validation, indent=2))
    if not validation["ok"]:
        raise SystemExit("Validation failed; nothing was imported.")
    if args.dry_run:
        return

    if not args.verify:
        print("\n== import ==")
        print(json.dumps(migrate(), indent=2))

    print("\n== verification ==")
    result = verify()
    print(json.dumps(result, indent=2))
    if not result["ok"]:
        raise SystemExit("Verification failed. Do not delete the JSON files.")
    print("\nVerified: every source record is present in the database.")


if __name__ == "__main__":
    main()
