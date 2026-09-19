"""Repositories: the only place that talks SQL.

Each repository owns one aggregate and exposes domain operations. Callers pass
and receive plain dictionaries in the application's existing camelCase shape, so
the rest of the app is unaffected by the storage change and nothing above this
layer imports SQLAlchemy.

Every method opens its own short transaction. Nothing here holds a session open
across a request.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

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


def new_id(size: int = 10) -> str:
    return secrets.token_hex(size)


def _iso(value: Optional[datetime]) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Row <-> dict mapping. Keeps the app's existing camelCase contract.
# ---------------------------------------------------------------------------

def user_to_dict(row: User) -> Dict[str, Any]:
    return {
        "id": row.id, "fullName": row.full_name, "email": row.email,
        "passwordHash": row.password_hash, "phone": row.phone, "city": row.city,
        "preferredContact": row.preferred_contact, "profilePicture": row.profile_picture,
        "createdAt": _iso(row.created_at),
    }


def doctor_to_dict(row: Doctor) -> Dict[str, Any]:
    return {
        "doctor_id": row.doctor_id, "doctor_name": row.doctor_name,
        "normalized_name": row.normalized_name, "specialty": row.specialty,
        "normalized_specialty": row.normalized_specialty, "location": row.location,
        "clinic": row.clinic, "source": row.source, "provider": row.provider,
        "provider_doctor_id": row.provider_doctor_id,
        "oladoc_profile_url": row.oladoc_profile_url,
        "oladoc_directory_url": row.oladoc_directory_url,
        "rating_label": row.rating_label, "ratingLabel": row.rating_label,
        "identity_strength": row.identity_strength,
        "booking_metadata": dict(row.booking_metadata or {}),
        "updatedAt": _iso(row.updated_at),
    }


APPOINTMENT_COLUMNS = {
    "specialistId": "specialist_id", "specialistName": "specialist_name",
    "specialistSpecialty": "specialist_specialty", "doctorId": "doctor_id",
    "providerDoctorId": "provider_doctor_id", "providerProfileUrl": "provider_profile_url",
    "clinicId": "clinic_id", "clinicName": "clinic_name", "clinicAddress": "clinic_address",
    "clinicFee": "clinic_fee", "date": "appointment_date", "time": "appointment_time",
    "timeLabel": "time_label", "timezone": "timezone_name", "reason": "reason",
    "bookingType": "booking_type", "paymentMethod": "payment_method",
    "bookingStatus": "booking_status", "status": "status",
    "bookingReference": "booking_reference", "providerAppointmentId": "provider_appointment_id",
    "confirmationReference": "confirmation_reference", "providerId": "provider_id",
    "providerName": "provider_name", "bookingSessionId": "booking_session_id",
    "slotId": "slot_id", "source": "source", "emailStatus": "email_status",
    "emailDetail": "email_detail", "provider": "provider_id",
}


def appointment_to_dict(row: Appointment) -> Dict[str, Any]:
    data = {
        "id": row.id, "userId": row.user_id, "doctorId": row.doctor_id or "",
        "specialistId": row.specialist_id, "specialistName": row.specialist_name,
        "doctorName": row.specialist_name,
        "specialistSpecialty": row.specialist_specialty,
        "providerDoctorId": row.provider_doctor_id,
        "providerProfileUrl": row.provider_profile_url,
        "clinicId": row.clinic_id, "clinicName": row.clinic_name,
        "clinicAddress": row.clinic_address, "clinicFee": row.clinic_fee,
        "date": row.appointment_date, "time": row.appointment_time,
        "timeLabel": row.time_label or row.appointment_time, "timezone": row.timezone_name,
        "reason": row.reason, "bookingType": row.booking_type,
        "paymentMethod": row.payment_method, "bookingStatus": row.booking_status,
        "status": row.status, "bookingReference": row.booking_reference,
        "providerAppointmentId": row.provider_appointment_id,
        "confirmationReference": row.confirmation_reference,
        "providerId": row.provider_id, "provider": row.provider_id,
        "providerName": row.provider_name, "bookingSessionId": row.booking_session_id,
        "slotId": row.slot_id, "source": row.source,
        "emailStatus": row.email_status, "emailDetail": row.email_detail,
        "createdAt": _iso(row.created_at), "updatedAt": _iso(row.updated_at),
    }
    if row.cancelled_at:
        data["cancelledAt"] = _iso(row.cancelled_at)
    data.update(row.extra or {})
    return data


def turn_to_dict(row: ConversationTurn, conversation_key: str, user_id: str) -> Dict[str, Any]:
    return {
        "id": row.id, "userId": user_id, "conversationId": conversation_key,
        "symptoms": row.symptoms, "internalAssessment": row.internal_assessment,
        "diseaseName": row.disease_name, "emergency": row.emergency,
        "recommendation": dict(row.recommendation or {}),
        "retrievedDocuments": list(row.retrieved_documents or []),
        "followUpQuestions": list(row.follow_up_questions or []),
        "questionAnswers": list(row.question_answers or []),
        "createdAt": _iso(row.created_at),
    }


def checklist_to_dict(row: Checklist) -> Dict[str, Any]:
    return {
        "id": row.id, "userId": row.user_id, "appointmentId": row.appointment_id,
        "completed": row.completed,
        "items": [
            {"id": item.item_key, "label": item.label, "completed": item.completed}
            for item in row.items
        ],
        "createdAt": _iso(row.created_at), "updatedAt": _iso(row.updated_at),
    }


def notification_to_dict(row: Notification) -> Dict[str, Any]:
    return {
        "id": row.id, "userId": row.user_id, "appointmentId": row.appointment_id or "",
        "channel": row.channel, "status": row.status, "recipient": row.recipient,
        "detail": row.detail, "message": row.message, "createdAt": _iso(row.created_at),
    }


def medical_to_dict(row: MedicalInformation) -> Dict[str, Any]:
    return {
        "id": row.id, "userId": row.user_id, "title": row.title, "category": row.category,
        "encryptedDetails": row.encrypted_details, "nonce": row.nonce,
        "encryption": row.encryption, "createdAt": _iso(row.created_at),
    }


def complaint_to_dict(row: Complaint) -> Dict[str, Any]:
    return {
        "id": row.id, "userId": row.user_id, "appointmentId": row.appointment_id,
        "subject": row.subject, "details": row.details, "category": row.category,
        "desiredResolution": row.desired_resolution, "status": row.status,
        "createdAt": _iso(row.created_at),
    }


def payment_to_dict(row: Payment) -> Dict[str, Any]:
    return {
        "id": row.id, "userId": row.user_id, "purpose": row.purpose, "amount": row.amount,
        "currency": row.currency, "status": row.status, "mode": row.mode,
        "clientSecret": "", "providerIntentId": row.provider_intent_id,
        "createdAt": _iso(row.created_at),
    }


def handoff_to_dict(row: RedirectHandoff) -> Dict[str, Any]:
    return {
        "id": row.id, "userId": row.user_id, "specialistId": row.specialist_id,
        "specialistName": row.specialist_name, "reason": row.reason,
        "preferredDate": row.preferred_date, "preferredTime": row.preferred_time,
        "referenceId": row.reference_id, "url": row.url, "summary": row.summary,
        "paymentId": row.payment_id, "createdAt": _iso(row.created_at),
    }


# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------

class UserRepository:
    def find_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        target = str(email or "").strip().lower()
        if not target:
            return None
        with session_scope() as session:
            row = session.scalar(select(User).where(func.lower(User.email) == target))
            return user_to_dict(row) if row else None

    def find_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.get(User, str(user_id or ""))
            return user_to_dict(row) if row else None

    def create(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with session_scope() as session:
            row = User(
                id=payload.get("id") or new_id(12),
                full_name=str(payload.get("fullName", "")).strip(),
                email=str(payload.get("email", "")).strip().lower(),
                password_hash=payload["passwordHash"],
                phone=str(payload.get("phone") or "").strip(),
                city=str(payload.get("city") or "Lahore").strip(),
                preferred_contact=payload.get("preferredContact") or "both",
                profile_picture=str(payload.get("profilePicture") or ""),
            )
            if created := _parse_dt(payload.get("createdAt")):
                row.created_at = created
            session.add(row)
            session.flush()
            return user_to_dict(row)

    def update(self, user_id: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        fields = {
            "fullName": "full_name", "phone": "phone", "city": "city",
            "preferredContact": "preferred_contact", "profilePicture": "profile_picture",
        }
        with session_scope() as session:
            row = session.get(User, str(user_id or ""))
            if not row:
                return None
            for key, column in fields.items():
                if key in payload and payload[key] is not None:
                    setattr(row, column, str(payload[key]).strip())
            session.flush()
            return user_to_dict(row)

    def count(self) -> int:
        with session_scope() as session:
            return int(session.scalar(select(func.count()).select_from(User)) or 0)


class DoctorRepository:
    def upsert(self, doctor: Dict[str, Any]) -> Dict[str, Any]:
        doctor_id = str(doctor.get("doctor_id") or "").strip()
        if not doctor_id:
            raise ValueError("A canonical doctor requires a doctor_id.")
        with session_scope() as session:
            row = session.get(Doctor, doctor_id) or Doctor(doctor_id=doctor_id)
            row.doctor_name = str(doctor.get("doctor_name", ""))
            row.normalized_name = str(doctor.get("normalized_name", ""))
            row.specialty = str(doctor.get("specialty", ""))
            row.normalized_specialty = str(doctor.get("normalized_specialty", ""))
            row.location = str(doctor.get("location", ""))
            row.clinic = str(doctor.get("clinic", ""))
            row.source = str(doctor.get("source", "live_search"))
            row.provider = str(doctor.get("provider", "oladoc"))
            row.provider_doctor_id = str(doctor.get("provider_doctor_id", ""))
            row.oladoc_profile_url = str(doctor.get("oladoc_profile_url", ""))
            row.oladoc_directory_url = str(doctor.get("oladoc_directory_url", ""))
            row.rating_label = str(doctor.get("rating_label") or doctor.get("ratingLabel") or "")
            row.identity_strength = str(doctor.get("identity_strength", "name_specialty"))
            row.booking_metadata = dict(doctor.get("booking_metadata") or {})
            session.add(row)
            session.flush()
            return doctor_to_dict(row)

    def upsert_many(self, doctors: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [self.upsert(d) for d in doctors if d.get("doctor_id")]

    def find(self, doctor_id: str) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.get(Doctor, str(doctor_id or ""))
            return doctor_to_dict(row) if row else None

    def count(self) -> int:
        with session_scope() as session:
            return int(session.scalar(select(func.count()).select_from(Doctor)) or 0)


class AppointmentRepository:
    def _apply(self, row: Appointment, payload: Dict[str, Any]) -> None:
        known = set(APPOINTMENT_COLUMNS) | {"id", "userId", "createdAt", "updatedAt", "cancelledAt"}
        for key, column in APPOINTMENT_COLUMNS.items():
            if key in payload and payload[key] is not None:
                setattr(row, column, str(payload[key]))
        if payload.get("cancelledAt"):
            row.cancelled_at = _parse_dt(payload["cancelledAt"])
        # Anything the domain adds later is preserved rather than dropped.
        extra = {k: v for k, v in payload.items() if k not in known}
        if extra:
            row.extra = {**(row.extra or {}), **extra}

    def create(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        with session_scope() as session:
            row = Appointment(
                id=payload.get("id") or new_id(10),
                user_id=user_id,
                appointment_date=str(payload.get("date", "")),
                appointment_time=str(payload.get("time", "")),
                booking_status=str(payload.get("bookingStatus", "SELECTED")),
                status=str(payload.get("status", "confirmed")),
            )
            doctor_id = str(payload.get("doctorId") or "").strip()
            if doctor_id and session.get(Doctor, doctor_id) is None:
                # Referencing an unregistered doctor would violate the FK; keep the
                # snapshot fields and leave the link null rather than lose the row.
                payload = {**payload, "doctorId": None}
            self._apply(row, payload)
            if created := _parse_dt(payload.get("createdAt")):
                row.created_at = created
            session.add(row)
            session.flush()
            return appointment_to_dict(row)

    def for_user(self, user_id: str) -> List[Dict[str, Any]]:
        with session_scope() as session:
            rows = session.scalars(
                select(Appointment)
                .where(Appointment.user_id == user_id)
                .order_by(Appointment.appointment_date, Appointment.appointment_time)
            ).all()
            return [appointment_to_dict(r) for r in rows]

    def find_for_user(self, appointment_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.scalar(
                select(Appointment).where(
                    Appointment.id == str(appointment_id), Appointment.user_id == user_id
                )
            )
            return appointment_to_dict(row) if row else None

    def find(self, appointment_id: str) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.get(Appointment, str(appointment_id or ""))
            return appointment_to_dict(row) if row else None

    def update(self, appointment_id: str, user_id: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.scalar(
                select(Appointment).where(
                    Appointment.id == str(appointment_id), Appointment.user_id == user_id
                )
            )
            if not row:
                return None
            self._apply(row, payload)
            session.flush()
            return appointment_to_dict(row)

    def slot_taken(self, doctor_id: str, date: str, time: str) -> bool:
        """Indexed conflict check, instead of scanning every appointment."""
        with session_scope() as session:
            found = session.scalar(
                select(func.count())
                .select_from(Appointment)
                .where(
                    Appointment.appointment_date == date,
                    Appointment.appointment_time == time,
                    Appointment.status.notin_(("cancelled", "failed")),
                    (Appointment.doctor_id == doctor_id) | (Appointment.specialist_id == doctor_id),
                )
            )
            return bool(found)

    def count(self) -> int:
        with session_scope() as session:
            return int(session.scalar(select(func.count()).select_from(Appointment)) or 0)


class ConversationRepository:
    """Conversations and their turns."""

    def _conversation(self, session: Session, user_id: str, key: str, create: bool = False) -> Optional[Conversation]:
        row = session.scalar(
            select(Conversation).where(
                Conversation.user_id == user_id, Conversation.conversation_key == key
            )
        )
        if row is None and create:
            row = Conversation(id=new_id(12), user_id=user_id, conversation_key=key)
            session.add(row)
            session.flush()
        return row

    def add_turn(self, user_id: str, conversation_key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        with session_scope() as session:
            conversation = self._conversation(session, user_id, conversation_key, create=True)
            position = int(session.scalar(
                select(func.count()).select_from(ConversationTurn)
                .where(ConversationTurn.conversation_id == conversation.id)
            ) or 0)
            turn = ConversationTurn(
                id=payload.get("id") or new_id(10),
                conversation_id=conversation.id,
                position=position,
                symptoms=str(payload.get("symptoms", "")),
                internal_assessment=str(payload.get("internalAssessment", "")),
                disease_name=str(payload.get("diseaseName", "")),
                emergency=bool(payload.get("emergency", False)),
                recommendation=payload.get("recommendation") or {},
                retrieved_documents=payload.get("retrievedDocuments") or [],
                follow_up_questions=payload.get("followUpQuestions") or [],
                question_answers=payload.get("questionAnswers") or [],
            )
            if created := _parse_dt(payload.get("createdAt")):
                turn.created_at = created
            session.add(turn)
            session.flush()
            return turn_to_dict(turn, conversation_key, user_id)

    def turns_for_user(self, user_id: str) -> List[Dict[str, Any]]:
        """Newest first, matching the order the UI previously relied on."""
        with session_scope() as session:
            rows = session.execute(
                select(ConversationTurn, Conversation.conversation_key)
                .join(Conversation, ConversationTurn.conversation_id == Conversation.id)
                .where(Conversation.user_id == user_id)
                .order_by(ConversationTurn.created_at.desc(), ConversationTurn.position.desc())
            ).all()
            return [turn_to_dict(turn, key, user_id) for turn, key in rows]

    def turns_for_conversation(self, user_id: str, conversation_key: str) -> List[Dict[str, Any]]:
        with session_scope() as session:
            conversation = self._conversation(session, user_id, conversation_key)
            if conversation is None:
                return []
            rows = session.scalars(
                select(ConversationTurn)
                .where(ConversationTurn.conversation_id == conversation.id)
                .order_by(ConversationTurn.position.desc())
            ).all()
            return [turn_to_dict(r, conversation_key, user_id) for r in rows]

    def get_intake(self, user_id: str, conversation_key: str) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            conversation = self._conversation(session, user_id, conversation_key)
            if conversation is None or conversation.intake is None:
                return None
            intake = conversation.intake
            return {
                "id": intake.id, "userId": user_id, "conversationId": conversation_key,
                "record": dict(intake.record or {}), "updatedAt": _iso(intake.updated_at),
            }

    def save_intake(self, user_id: str, conversation_key: str, record: Dict[str, Any]) -> Dict[str, Any]:
        with session_scope() as session:
            conversation = self._conversation(session, user_id, conversation_key, create=True)
            intake = conversation.intake
            if intake is None:
                intake = SymptomIntake(
                    id=new_id(10), user_id=user_id, conversation_id=conversation.id, record={}
                )
                session.add(intake)
            intake.record = dict(record or {})
            session.flush()
            return {
                "id": intake.id, "userId": user_id, "conversationId": conversation_key,
                "record": dict(intake.record), "updatedAt": _iso(intake.updated_at),
            }

    def counts(self) -> Dict[str, int]:
        with session_scope() as session:
            return {
                "conversations": int(session.scalar(select(func.count()).select_from(Conversation)) or 0),
                "turns": int(session.scalar(select(func.count()).select_from(ConversationTurn)) or 0),
            }


class ChecklistRepository:
    def create(self, user_id: str, appointment_id: str, items: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        with session_scope() as session:
            session.execute(delete(Checklist).where(Checklist.appointment_id == appointment_id))
            row = Checklist(id=new_id(10), user_id=user_id, appointment_id=appointment_id, completed=False)
            for position, item in enumerate(items):
                row.items.append(ChecklistItem(
                    item_key=str(item.get("id", f"item-{position}")),
                    label=str(item.get("label", "")),
                    completed=bool(item.get("completed", False)),
                    position=position,
                ))
            session.add(row)
            session.flush()
            return checklist_to_dict(row)

    def find_by_appointment(self, appointment_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.scalar(
                select(Checklist)
                .options(selectinload(Checklist.items))
                .where(Checklist.appointment_id == appointment_id, Checklist.user_id == user_id)
            )
            return checklist_to_dict(row) if row else None

    def find_for_user(self, checklist_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.scalar(
                select(Checklist)
                .options(selectinload(Checklist.items))
                .where(Checklist.id == checklist_id, Checklist.user_id == user_id)
            )
            return checklist_to_dict(row) if row else None

    def update_items(self, checklist_id: str, user_id: str, items: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.scalar(
                select(Checklist)
                .options(selectinload(Checklist.items))
                .where(Checklist.id == checklist_id, Checklist.user_id == user_id)
            )
            if not row:
                return None
            wanted = {str(item.get("id")): bool(item.get("completed")) for item in items}
            for item in row.items:
                if item.item_key in wanted:
                    item.completed = wanted[item.item_key]
            row.completed = bool(row.items) and all(i.completed for i in row.items)
            session.flush()
            return checklist_to_dict(row)


class NotificationRepository:
    def create(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        with session_scope() as session:
            appointment_id = str(payload.get("appointmentId") or "") or None
            if appointment_id and session.get(Appointment, appointment_id) is None:
                appointment_id = None
            row = Notification(
                id=payload.get("id") or new_id(10), user_id=user_id, appointment_id=appointment_id,
                channel=str(payload.get("channel", "email")), status=str(payload.get("status", "pending")),
                recipient=str(payload.get("recipient", "")), detail=str(payload.get("detail", "")),
                message=str(payload.get("message", "")),
            )
            if created := _parse_dt(payload.get("createdAt")):
                row.created_at = created
            session.add(row)
            session.flush()
            return notification_to_dict(row)

    def for_user(self, user_id: str) -> List[Dict[str, Any]]:
        with session_scope() as session:
            rows = session.scalars(
                select(Notification).where(Notification.user_id == user_id)
                .order_by(Notification.created_at.desc())
            ).all()
            return [notification_to_dict(r) for r in rows]

    def update_status(self, notification_id: str, status: str) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.get(Notification, str(notification_id or ""))
            if not row:
                return None
            row.status = status
            session.flush()
            return notification_to_dict(row)

    def count(self) -> int:
        with session_scope() as session:
            return int(session.scalar(select(func.count()).select_from(Notification)) or 0)


class _SimpleUserOwnedRepository:
    """Shared implementation for the straightforward user-owned collections."""

    model: Any = None
    to_dict: Any = None

    def _build(self, user_id: str, payload: Dict[str, Any]) -> Any:
        raise NotImplementedError

    def create(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        with session_scope() as session:
            row = self._build(user_id, payload)
            if created := _parse_dt(payload.get("createdAt")):
                row.created_at = created
            session.add(row)
            session.flush()
            return type(self).to_dict(row)

    def for_user(self, user_id: str) -> List[Dict[str, Any]]:
        with session_scope() as session:
            rows = session.scalars(
                select(self.model).where(self.model.user_id == user_id)
                .order_by(self.model.created_at.desc())
            ).all()
            return [type(self).to_dict(r) for r in rows]

    def count(self) -> int:
        with session_scope() as session:
            return int(session.scalar(select(func.count()).select_from(self.model)) or 0)


class MedicalInformationRepository(_SimpleUserOwnedRepository):
    model = MedicalInformation
    to_dict = staticmethod(medical_to_dict)

    def _build(self, user_id, payload):
        return MedicalInformation(
            id=payload.get("id") or new_id(10), user_id=user_id,
            title=str(payload.get("title", "")), category=str(payload.get("category", "general")),
            encrypted_details=str(payload.get("encryptedDetails", "")),
            nonce=str(payload.get("nonce", "")),
            encryption=str(payload.get("encryption", "AES-256-GCM")),
        )


class ComplaintRepository(_SimpleUserOwnedRepository):
    model = Complaint
    to_dict = staticmethod(complaint_to_dict)

    def _build(self, user_id, payload):
        return Complaint(
            id=payload.get("id") or new_id(10), user_id=user_id,
            appointment_id=str(payload.get("appointmentId") or "") or None,
            subject=str(payload.get("subject", "")), details=str(payload.get("details", "")),
            category=str(payload.get("category", "general")),
            desired_resolution=str(payload.get("desiredResolution") or ""),
            status=str(payload.get("status", "open")),
        )


class PaymentRepository(_SimpleUserOwnedRepository):
    model = Payment
    to_dict = staticmethod(payment_to_dict)

    def _build(self, user_id, payload):
        return Payment(
            id=payload.get("id") or new_id(10), user_id=user_id,
            purpose=str(payload.get("purpose", "")), amount=int(payload.get("amount") or 0),
            currency=str(payload.get("currency", "pkr")),
            status=str(payload.get("status", "requires_confirmation")),
            provider_intent_id=str(payload.get("id_provider") or payload.get("providerIntentId") or ""),
            mode=str(payload.get("mode", "test")),
        )


class RedirectHandoffRepository(_SimpleUserOwnedRepository):
    model = RedirectHandoff
    to_dict = staticmethod(handoff_to_dict)

    def _build(self, user_id, payload):
        return RedirectHandoff(
            id=payload.get("id") or new_id(10), user_id=user_id,
            specialist_id=str(payload.get("specialistId", "")),
            specialist_name=str(payload.get("specialistName", "")),
            reason=str(payload.get("reason", "")),
            preferred_date=str(payload.get("preferredDate") or ""),
            preferred_time=str(payload.get("preferredTime") or ""),
            reference_id=str(payload.get("referenceId", "")),
            url=str(payload.get("url", "")), summary=str(payload.get("summary", "")),
            payment_id=payload.get("paymentId") or None,
        )
