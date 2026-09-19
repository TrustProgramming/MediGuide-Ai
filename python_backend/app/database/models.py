"""Relational schema for MediGuide.

Designed from the domain rather than by copying each JSON file into a table:

* ``chat-sessions.json`` held one row per conversational turn with the
  conversation id repeated, so it becomes ``conversations`` + ``conversation_turns``.
* ``checklists.json`` embedded its items in an array; per-item completion is
  real state, so items become rows.
* Small fixed-shape lists that are always read with their parent (a turn's
  follow-up questions, a doctor's booking metadata) stay as JSON columns. They
  are never queried by their contents, so splitting them would add joins
  without buying anything.

Column types are chosen to work unchanged on SQLite and PostgreSQL.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """JSON maps to JSONB on PostgreSQL and TEXT-backed JSON on SQLite."""

    type_annotation_map = {dict: JSON, list: JSON}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    city: Mapped[str] = mapped_column(String(120), default="Lahore", nullable=False)
    preferred_contact: Mapped[str] = mapped_column(String(16), default="both", nullable=False)
    profile_picture: Mapped[str] = mapped_column(Text, default="", nullable=False)

    appointments: Mapped[list["Appointment"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint("preferred_contact in ('email','whatsapp','phone','both')", name="ck_users_contact"),
        Index("ix_users_email_lower", "email"),
    )


class Doctor(Base, TimestampMixin):
    """Canonical doctor identity shared by search, booking and verification."""

    __tablename__ = "doctors"

    doctor_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    doctor_name: Mapped[str] = mapped_column(String(200), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    specialty: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    normalized_specialty: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    location: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    clinic: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    source: Mapped[str] = mapped_column(String(40), default="live_search", nullable=False)
    provider: Mapped[str] = mapped_column(String(40), default="oladoc", nullable=False)
    provider_doctor_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    oladoc_profile_url: Mapped[str] = mapped_column(Text, default="", nullable=False)
    oladoc_directory_url: Mapped[str] = mapped_column(Text, default="", nullable=False)
    rating_label: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    identity_strength: Mapped[str] = mapped_column(String(32), default="name_specialty", nullable=False)
    booking_metadata: Mapped[dict] = mapped_column(default=dict, nullable=False)

    __table_args__ = (
        # Same-name doctors are expected, so the name is indexed but not unique.
        Index("ix_doctors_normalized_name", "normalized_name"),
        Index("ix_doctors_provider_doctor_id", "provider_doctor_id"),
    )


class Appointment(Base, TimestampMixin):
    __tablename__ = "appointments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Kept nullable and ON DELETE SET NULL: removing a doctor from the registry
    # must never delete a patient's appointment history.
    doctor_id: Mapped[Optional[str]] = mapped_column(
        String(128), ForeignKey("doctors.doctor_id", ondelete="SET NULL"), nullable=True
    )

    # Snapshots, so history stays readable if the doctor record later changes.
    specialist_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    specialist_name: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    specialist_specialty: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    provider_doctor_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    provider_profile_url: Mapped[str] = mapped_column(Text, default="", nullable=False)
    clinic_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    clinic_name: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    clinic_address: Mapped[str] = mapped_column(Text, default="", nullable=False)
    clinic_fee: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    appointment_date: Mapped[str] = mapped_column(String(10), nullable=False)
    appointment_time: Mapped[str] = mapped_column(String(8), nullable=False)
    time_label: Mapped[str] = mapped_column(String(24), default="", nullable=False)
    timezone_name: Mapped[str] = mapped_column(String(64), default="Asia/Karachi", nullable=False)

    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    booking_type: Mapped[str] = mapped_column(String(24), default="physical", nullable=False)
    payment_method: Mapped[str] = mapped_column(String(32), default="pay-at-clinic", nullable=False)
    booking_status: Mapped[str] = mapped_column(String(24), default="SELECTED", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="selected", nullable=False)
    booking_reference: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    provider_appointment_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    confirmation_reference: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    provider_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    provider_name: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    booking_session_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    slot_id: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    source: Mapped[str] = mapped_column(String(80), default="", nullable=False)

    email_status: Mapped[str] = mapped_column(String(24), default="PENDING", nullable=False)
    email_detail: Mapped[str] = mapped_column(Text, default="", nullable=False)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    extra: Mapped[dict] = mapped_column(default=dict, nullable=False)

    user: Mapped["User"] = relationship(back_populates="appointments")
    checklist: Mapped[Optional["Checklist"]] = relationship(
        back_populates="appointment", cascade="all, delete-orphan", passive_deletes=True, uselist=False
    )

    __table_args__ = (
        CheckConstraint(
            "booking_status in ('SELECTED','IN_PROGRESS','AWAITING_OTP','CONFIRMED','FAILED','CANCELLED')",
            name="ck_appointments_booking_status",
        ),
        CheckConstraint("email_status in ('PENDING','SENT','FAILED','NOT_CONFIGURED')", name="ck_appointments_email_status"),
        # The listing query is "this user's appointments, in date order".
        Index("ix_appointments_user_date", "user_id", "appointment_date", "appointment_time"),
        # Slot-conflict lookup. The date/time pair leads, because a booking may
        # identify its doctor through either doctor_id or specialist_id and an OR
        # across those two columns cannot use an index that leads with one of them.
        Index("ix_appointments_slot", "appointment_date", "appointment_time"),
        Index("ix_appointments_doctor", "doctor_id"),
        Index("ix_appointments_specialist", "specialist_id"),
    )


class Conversation(Base, TimestampMixin):
    """One AI health-chat conversation. Turns hang off this."""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    conversation_key: Mapped[str] = mapped_column(String(64), nullable=False)

    user: Mapped["User"] = relationship(back_populates="conversations")
    turns: Mapped[list["ConversationTurn"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan",
        passive_deletes=True, order_by="ConversationTurn.position",
    )
    intake: Mapped[Optional["SymptomIntake"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", passive_deletes=True, uselist=False
    )

    __table_args__ = (
        UniqueConstraint("user_id", "conversation_key", name="uq_conversations_user_key"),
        Index("ix_conversations_user", "user_id"),
    )


class ConversationTurn(Base, TimestampMixin):
    __tablename__ = "conversation_turns"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    symptoms: Mapped[str] = mapped_column(Text, default="", nullable=False)
    internal_assessment: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    disease_name: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    emergency: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Always read with the turn and never queried by content: JSON is right here.
    recommendation: Mapped[dict] = mapped_column(default=dict, nullable=False)
    retrieved_documents: Mapped[list] = mapped_column(default=list, nullable=False)
    follow_up_questions: Mapped[list] = mapped_column(default=list, nullable=False)
    question_answers: Mapped[list] = mapped_column(default=list, nullable=False)

    conversation: Mapped["Conversation"] = relationship(back_populates="turns")

    __table_args__ = (Index("ix_turns_conversation_position", "conversation_id", "position"),)


class SymptomIntake(Base, TimestampMixin):
    """The structured symptom record built up across a conversation."""

    __tablename__ = "symptom_intakes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    conversation_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    record: Mapped[dict] = mapped_column(default=dict, nullable=False)

    conversation: Mapped["Conversation"] = relationship(back_populates="intake")


class Checklist(Base, TimestampMixin):
    __tablename__ = "checklists"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    appointment_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("appointments.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    appointment: Mapped["Appointment"] = relationship(back_populates="checklist")
    items: Mapped[list["ChecklistItem"]] = relationship(
        back_populates="checklist", cascade="all, delete-orphan",
        passive_deletes=True, order_by="ChecklistItem.position",
    )


class ChecklistItem(Base):
    """Per-item completion is real state, so items are rows rather than an array."""

    __tablename__ = "checklist_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    checklist_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("checklists.id", ondelete="CASCADE"), nullable=False
    )
    item_key: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    checklist: Mapped["Checklist"] = relationship(back_populates="items")

    __table_args__ = (
        UniqueConstraint("checklist_id", "item_key", name="uq_checklist_item_key"),
        Index("ix_checklist_items_checklist", "checklist_id", "position"),
    )


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    appointment_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("appointments.id", ondelete="SET NULL"), nullable=True
    )
    channel: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    recipient: Mapped[str] = mapped_column(String(320), default="", nullable=False)
    detail: Mapped[str] = mapped_column(Text, default="", nullable=False)
    message: Mapped[str] = mapped_column(Text, default="", nullable=False)

    __table_args__ = (
        CheckConstraint("channel in ('email','whatsapp','sms')", name="ck_notifications_channel"),
        Index("ix_notifications_user_created", "user_id", "created_at"),
    )


class MedicalInformation(Base, TimestampMixin):
    """Patient-entered records. Details stay encrypted at rest."""

    __tablename__ = "medical_information"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(64), default="general", nullable=False)
    encrypted_details: Mapped[str] = mapped_column(Text, default="", nullable=False)
    nonce: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    encryption: Mapped[str] = mapped_column(String(32), default="AES-256-GCM", nullable=False)

    __table_args__ = (Index("ix_medical_information_user", "user_id"),)


class Complaint(Base, TimestampMixin):
    __tablename__ = "complaints"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    appointment_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("appointments.id", ondelete="SET NULL"), nullable=True
    )
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    details: Mapped[str] = mapped_column(Text, default="", nullable=False)
    category: Mapped[str] = mapped_column(String(64), default="general", nullable=False)
    desired_resolution: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="open", nullable=False)

    __table_args__ = (Index("ix_complaints_user", "user_id"),)


class Payment(Base, TimestampMixin):
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    purpose: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    amount: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="pkr", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="requires_confirmation", nullable=False)
    provider_intent_id: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    mode: Mapped[str] = mapped_column(String(16), default="test", nullable=False)

    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_payments_amount_non_negative"),
        Index("ix_payments_user", "user_id"),
    )


class RedirectHandoff(Base, TimestampMixin):
    __tablename__ = "redirect_handoffs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    specialist_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    specialist_name: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    preferred_date: Mapped[str] = mapped_column(String(10), default="", nullable=False)
    preferred_time: Mapped[str] = mapped_column(String(8), default="", nullable=False)
    reference_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    url: Mapped[str] = mapped_column(Text, default="", nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    payment_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    __table_args__ = (Index("ix_redirect_handoffs_user", "user_id"),)
