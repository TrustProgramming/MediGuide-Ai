"""Database layer: schema constraints, repositories, relationships, transactions."""

from __future__ import annotations

import threading

import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError

from app.database import repositories as repo
from app.database.engine import get_engine, healthcheck, session_scope
from app.database.models import (
    Appointment,
    Checklist,
    ChecklistItem,
    Conversation,
    ConversationTurn,
    Notification,
    User,
)
from app.db import db


def make_user(email="patient@example.com", name="Ayesha Khan"):
    return db.create_user({"fullName": name, "email": email, "passwordHash": "salt:hash", "phone": "+92300"})


# --------------------------------------------------------------------------
# Connection and schema
# --------------------------------------------------------------------------

def test_connection_is_healthy():
    assert healthcheck()["status"] == "ok"


def test_expected_tables_exist():
    names = set(inspect(get_engine()).get_table_names())
    for table in ("users", "doctors", "appointments", "conversations", "conversation_turns",
                  "symptom_intakes", "checklists", "checklist_items", "notifications",
                  "medical_information", "complaints", "payments", "redirect_handoffs"):
        assert table in names, f"missing table {table}"


def test_indexes_exist_on_the_hot_paths():
    insp = inspect(get_engine())
    appointment_indexes = {i["name"] for i in insp.get_indexes("appointments")}
    assert "ix_appointments_user_date" in appointment_indexes
    assert "ix_appointments_slot" in appointment_indexes
    assert "ix_turns_conversation_position" in {i["name"] for i in insp.get_indexes("conversation_turns")}


# --------------------------------------------------------------------------
# CRUD
# --------------------------------------------------------------------------

def test_user_round_trip():
    created = make_user()
    assert created["id"]
    fetched = db.find_user_by_id(created["id"])
    assert fetched["email"] == "patient@example.com"
    assert db.find_user_by_email("PATIENT@EXAMPLE.COM")["id"] == created["id"], "lookup must be case-insensitive"

    updated = db.update_user(created["id"], {"city": "Karachi", "phone": "+92111"})
    assert updated["city"] == "Karachi"
    assert db.find_user_by_id(created["id"])["phone"] == "+92111"


def test_appointment_round_trip_and_ordering():
    user = make_user()
    for date, time in [("2026-10-03", "11:00"), ("2026-10-01", "09:30"), ("2026-10-01", "08:00")]:
        db.create_appointment(user["id"], {
            "date": date, "time": time, "specialistName": "Dr. Test", "status": "confirmed",
        })
    listed = db.get_appointments_for_user(user["id"])
    assert [(a["date"], a["time"]) for a in listed] == [
        ("2026-10-01", "08:00"), ("2026-10-01", "09:30"), ("2026-10-03", "11:00"),
    ], "appointments must come back in date/time order"


def test_appointment_update_is_scoped_to_its_owner():
    owner, other = make_user("a@example.com"), make_user("b@example.com")
    appointment = db.create_appointment(owner["id"], {"date": "2026-10-01", "time": "09:00"})
    assert db.update_appointment(appointment["id"], other["id"], {"reason": "hijack"}) is None
    assert db.find_appointment_for_user(appointment["id"], other["id"]) is None
    assert db.update_appointment(appointment["id"], owner["id"], {"reason": "ok"})["reason"] == "ok"


def test_unknown_fields_are_preserved_not_dropped():
    user = make_user()
    appointment = db.create_appointment(user["id"], {
        "date": "2026-10-01", "time": "09:00", "somethingNew": "keep me", "customCount": 3,
    })
    stored = db.find_appointment_for_user(appointment["id"], user["id"])
    assert stored["somethingNew"] == "keep me"
    assert stored["customCount"] == 3


# --------------------------------------------------------------------------
# Constraints
# --------------------------------------------------------------------------

def test_duplicate_email_is_rejected_by_the_database():
    make_user("dupe@example.com")
    with pytest.raises(IntegrityError):
        make_user("dupe@example.com", name="Someone Else")


def test_appointment_requires_a_real_user():
    with pytest.raises(IntegrityError):
        db.create_appointment("no-such-user", {"date": "2026-10-01", "time": "09:00"})


def test_invalid_booking_status_is_rejected():
    user = make_user()
    with pytest.raises(IntegrityError):
        db.create_appointment(user["id"], {
            "date": "2026-10-01", "time": "09:00", "bookingStatus": "NOT_A_STATUS",
        })


def test_invalid_notification_channel_is_rejected():
    user = make_user()
    with pytest.raises(IntegrityError):
        db.create_notification(user["id"], {"channel": "carrier-pigeon", "status": "pending"})


def test_negative_payment_amount_is_rejected():
    user = make_user()
    with pytest.raises(IntegrityError):
        db.create_payment(user["id"], {"purpose": "fee", "amount": -1})


def test_one_checklist_per_appointment():
    user = make_user()
    appointment = db.create_appointment(user["id"], {"date": "2026-10-01", "time": "09:00"})
    first = db.create_checklist(user["id"], appointment["id"], [{"id": "a", "label": "A"}])
    second = db.create_checklist(user["id"], appointment["id"], [{"id": "b", "label": "B"}])
    assert first["id"] != second["id"]
    with session_scope() as session:
        assert session.scalar(
            select(func.count()).select_from(Checklist).where(Checklist.appointment_id == appointment["id"])
        ) == 1


# --------------------------------------------------------------------------
# Relationships and cascades
# --------------------------------------------------------------------------

def test_deleting_a_user_removes_their_data():
    user = make_user()
    appointment = db.create_appointment(user["id"], {"date": "2026-10-01", "time": "09:00"})
    db.create_checklist(user["id"], appointment["id"], [{"id": "x", "label": "X"}])
    db.create_record("chat_sessions", user["id"], {"conversationId": "c1", "symptoms": "headache"})

    with session_scope() as session:
        session.delete(session.get(User, user["id"]))

    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(Appointment)) == 0
        assert session.scalar(select(func.count()).select_from(Checklist)) == 0
        assert session.scalar(select(func.count()).select_from(ChecklistItem)) == 0
        assert session.scalar(select(func.count()).select_from(ConversationTurn)) == 0


def test_removing_a_doctor_keeps_the_appointment_history():
    user = make_user()
    doctor = db.upsert_doctor({
        "doctor_id": "oladoc:1", "doctor_name": "Dr. One", "specialty": "Urologist",
    })
    appointment = db.create_appointment(user["id"], {
        "date": "2026-10-01", "time": "09:00", "doctorId": doctor["doctor_id"],
        "specialistName": "Dr. One",
    })
    with session_scope() as session:
        from app.database.models import Doctor

        session.delete(session.get(Doctor, "oladoc:1"))

    kept = db.find_appointment_for_user(appointment["id"], user["id"])
    assert kept is not None, "history must survive the doctor being removed"
    assert kept["doctorId"] == ""      # link cleared
    assert kept["specialistName"] == "Dr. One"  # snapshot kept


def test_conversation_turns_are_grouped_and_ordered():
    user = make_user()
    for i in range(3):
        db.create_record("chat_sessions", user["id"], {"conversationId": "conv-a", "symptoms": f"turn {i}"})
    db.create_record("chat_sessions", user["id"], {"conversationId": "conv-b", "symptoms": "other"})

    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(Conversation)) == 2
        assert session.scalar(select(func.count()).select_from(ConversationTurn)) == 4
        positions = session.scalars(
            select(ConversationTurn.position)
            .join(Conversation)
            .where(Conversation.conversation_key == "conv-a")
            .order_by(ConversationTurn.position)
        ).all()
        assert positions == [0, 1, 2]

    assert len(db.get_conversation_turns(user["id"], "conv-a")) == 3


def test_checklist_items_are_rows_with_individual_state():
    user = make_user()
    appointment = db.create_appointment(user["id"], {"date": "2026-10-01", "time": "09:00"})
    checklist = db.create_checklist(user["id"], appointment["id"], [
        {"id": "docs", "label": "Bring documents"},
        {"id": "arrive", "label": "Arrive early"},
    ])
    assert checklist["completed"] is False

    updated = db.update_checklist(checklist["id"], user["id"], [
        {"id": "docs", "completed": True}, {"id": "arrive", "completed": False},
    ])
    assert [i["completed"] for i in updated["items"]] == [True, False]
    assert updated["completed"] is False

    done = db.update_checklist(checklist["id"], user["id"], [
        {"id": "docs", "completed": True}, {"id": "arrive", "completed": True},
    ])
    assert done["completed"] is True


def test_intake_is_one_per_conversation():
    user = make_user()
    db.save_intake(user["id"], "conv-1", {"main_symptom": "headache"})
    db.save_intake(user["id"], "conv-1", {"main_symptom": "headache", "severity": 7})
    stored = db.get_intake(user["id"], "conv-1")
    assert stored["record"]["severity"] == 7
    with session_scope() as session:
        from app.database.models import SymptomIntake

        assert session.scalar(select(func.count()).select_from(SymptomIntake)) == 1


# --------------------------------------------------------------------------
# Transactions and concurrency
# --------------------------------------------------------------------------

def test_a_failed_transaction_writes_nothing():
    user = make_user()
    before = db.counts()["appointments"]
    with pytest.raises(RuntimeError):
        with session_scope() as session:
            session.add(Appointment(
                id="rollback-me", user_id=user["id"], appointment_date="2026-10-01", appointment_time="09:00",
            ))
            session.flush()
            raise RuntimeError("boom")
    assert db.counts()["appointments"] == before
    assert db.find_appointment("rollback-me") is None


def test_concurrent_writes_all_land():
    """WAL plus a busy timeout should let parallel request threads write."""
    user = make_user()
    errors = []

    def write(index):
        try:
            db.create_appointment(user["id"], {"date": "2026-11-01", "time": f"{9 + index:02d}:00"})
        except Exception as exc:  # pragma: no cover - only on failure
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"concurrent writes failed: {errors[:2]}"
    assert len(db.get_appointments_for_user(user["id"])) == 8


# --------------------------------------------------------------------------
# Queries
# --------------------------------------------------------------------------

def test_slot_conflict_uses_an_indexed_lookup():
    user = make_user()
    db.create_appointment(user["id"], {
        "date": "2026-10-05", "time": "10:30", "specialistId": "sp-primary", "status": "confirmed",
    })
    assert db.is_slot_taken("sp-primary", "2026-10-05", "10:30") is True
    assert db.is_slot_taken("sp-primary", "2026-10-05", "11:00") is False
    assert db.is_slot_taken("sp-other", "2026-10-05", "10:30") is False


def test_cancelled_appointments_free_the_slot():
    user = make_user()
    appointment = db.create_appointment(user["id"], {
        "date": "2026-10-05", "time": "10:30", "specialistId": "sp-primary", "status": "confirmed",
    })
    assert db.is_slot_taken("sp-primary", "2026-10-05", "10:30") is True
    db.update_appointment(appointment["id"], user["id"], {"status": "cancelled"})
    assert db.is_slot_taken("sp-primary", "2026-10-05", "10:30") is False


def test_records_are_scoped_per_user():
    a, b = make_user("a@example.com"), make_user("b@example.com")
    db.create_record("complaints", a["id"], {"subject": "A complaint", "details": "x"})
    db.create_record("complaints", b["id"], {"subject": "B complaint", "details": "y"})
    assert [c["subject"] for c in db.get_records("complaints", a["id"])] == ["A complaint"]
    assert [c["subject"] for c in db.get_records("complaints", b["id"])] == ["B complaint"]


def test_notifications_come_back_newest_first():
    user = make_user()
    for i in range(3):
        db.create_notification(user["id"], {"channel": "email", "detail": f"n{i}", "status": "sent"})
    details = [n["detail"] for n in db.get_notifications_for_user(user["id"])]
    assert details[0] == "n2"


def test_unknown_collection_is_rejected():
    user = make_user()
    with pytest.raises(ValueError):
        db.get_records("not_a_collection", user["id"])
    with pytest.raises(ValueError):
        db.create_record("not_a_collection", user["id"], {})


def test_doctor_upsert_does_not_duplicate():
    db.upsert_doctor({"doctor_id": "oladoc:9", "doctor_name": "Dr. Nine", "specialty": "ENT"})
    db.upsert_doctor({"doctor_id": "oladoc:9", "doctor_name": "Dr. Nine Updated", "specialty": "ENT"})
    assert db.counts()["doctors"] == 1
    assert db.find_doctor("oladoc:9")["doctor_name"] == "Dr. Nine Updated"


def test_doctor_without_id_is_refused():
    with pytest.raises(ValueError):
        db.upsert_doctor({"doctor_name": "Nameless"})
