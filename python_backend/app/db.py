"""Database facade.

The application used to talk to a JSON-file store through a module-level ``db``
object. That object is now backed by SQL repositories: the method names and the
dictionary shapes are unchanged, so call sites did not have to move, but every
read and write goes through the database.

New code should prefer the repositories in ``app.database.repositories``
directly. This facade exists so the migration did not require rewriting every
caller at once.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

from .database import repositories as repo
from .database.engine import get_engine, healthcheck, session_scope

logger = logging.getLogger(__name__)


class Database:
    """Thin delegation layer over the repositories."""

    def __init__(self) -> None:
        self.users = repo.UserRepository()
        self.doctors_repo = repo.DoctorRepository()
        self.appointments_repo = repo.AppointmentRepository()
        self.conversations = repo.ConversationRepository()
        self.checklists_repo = repo.ChecklistRepository()
        self.notifications_repo = repo.NotificationRepository()
        self.medical_information_repo = repo.MedicalInformationRepository()
        self.complaints_repo = repo.ComplaintRepository()
        self.payments_repo = repo.PaymentRepository()
        self.redirect_handoffs_repo = repo.RedirectHandoffRepository()

    # --- users -------------------------------------------------------------
    def find_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        return self.users.find_by_email(email)

    def find_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        return self.users.find_by_id(user_id)

    def create_user(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.users.create(payload)

    def update_user(self, user_id: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return self.users.update(user_id, payload)

    # --- doctors -----------------------------------------------------------
    def upsert_doctor(self, doctor: Dict[str, Any]) -> Dict[str, Any]:
        return self.doctors_repo.upsert(doctor)

    def upsert_doctors(self, doctors: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return self.doctors_repo.upsert_many(doctors)

    def find_doctor(self, doctor_id: str) -> Optional[Dict[str, Any]]:
        return self.doctors_repo.find(doctor_id)

    # --- appointments ------------------------------------------------------
    def create_appointment(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.appointments_repo.create(user_id, payload)

    def get_appointments_for_user(self, user_id: str) -> List[Dict[str, Any]]:
        return self.appointments_repo.for_user(user_id)

    def find_appointment_for_user(self, appointment_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        return self.appointments_repo.find_for_user(appointment_id, user_id)

    def find_appointment(self, appointment_id: str) -> Optional[Dict[str, Any]]:
        return self.appointments_repo.find(appointment_id)

    def update_appointment(self, appointment_id: str, user_id: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return self.appointments_repo.update(appointment_id, user_id, payload)

    def is_slot_taken(self, specialist_id: str, date: str, time: str) -> bool:
        return self.appointments_repo.slot_taken(specialist_id, date, time)

    # --- conversations, medical information, complaints --------------------
    def create_record(self, collection: str, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if collection == "chat_sessions":
            return self.conversations.add_turn(user_id, str(payload.get("conversationId", "")), payload)
        if collection == "medical_information":
            return self.medical_information_repo.create(user_id, payload)
        if collection == "complaints":
            return self.complaints_repo.create(user_id, payload)
        raise ValueError(f"Unknown collection: {collection}")

    def get_records(self, collection: str, user_id: str) -> List[Dict[str, Any]]:
        if collection == "chat_sessions":
            return self.conversations.turns_for_user(user_id)
        if collection == "medical_information":
            return self.medical_information_repo.for_user(user_id)
        if collection == "complaints":
            return self.complaints_repo.for_user(user_id)
        raise ValueError(f"Unknown collection: {collection}")

    def get_conversation_turns(self, user_id: str, conversation_key: str) -> List[Dict[str, Any]]:
        return self.conversations.turns_for_conversation(user_id, conversation_key)

    def get_intake(self, user_id: str, conversation_id: str) -> Optional[Dict[str, Any]]:
        return self.conversations.get_intake(user_id, conversation_id)

    def save_intake(self, user_id: str, conversation_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
        return self.conversations.save_intake(user_id, conversation_id, record)

    # --- checklists --------------------------------------------------------
    def create_checklist(self, user_id: str, appointment_id: str, items: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        return self.checklists_repo.create(user_id, appointment_id, items)

    def find_checklist_by_appointment(self, appointment_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        return self.checklists_repo.find_by_appointment(appointment_id, user_id)

    def find_checklist_for_user(self, checklist_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        return self.checklists_repo.find_for_user(checklist_id, user_id)

    def update_checklist(self, checklist_id: str, user_id: str, items: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        return self.checklists_repo.update_items(checklist_id, user_id, items)

    # --- notifications -----------------------------------------------------
    def create_notification(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.notifications_repo.create(user_id, payload)

    def get_notifications_for_user(self, user_id: str) -> List[Dict[str, Any]]:
        return self.notifications_repo.for_user(user_id)

    def update_notification_status(self, notification_id: str, status: str) -> Optional[Dict[str, Any]]:
        return self.notifications_repo.update_status(notification_id, status)

    # --- payments and handoffs --------------------------------------------
    def create_payment(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.payments_repo.create(user_id, payload)

    def get_payments_for_user(self, user_id: str) -> List[Dict[str, Any]]:
        return self.payments_repo.for_user(user_id)

    def create_redirect_handoff(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.redirect_handoffs_repo.create(user_id, payload)

    def get_redirect_handoffs_for_user(self, user_id: str) -> List[Dict[str, Any]]:
        return self.redirect_handoffs_repo.for_user(user_id)

    # --- diagnostics -------------------------------------------------------
    def health(self) -> Dict[str, Any]:
        return healthcheck()

    def counts(self) -> Dict[str, int]:
        return {
            "users": self.users.count(),
            "doctors": self.doctors_repo.count(),
            "appointments": self.appointments_repo.count(),
            "notifications": self.notifications_repo.count(),
            "medical_information": self.medical_information_repo.count(),
            "complaints": self.complaints_repo.count(),
            "payments": self.payments_repo.count(),
            "redirect_handoffs": self.redirect_handoffs_repo.count(),
            **self.conversations.counts(),
        }


db = Database()

__all__ = ["db", "Database", "get_engine", "session_scope", "healthcheck"]
