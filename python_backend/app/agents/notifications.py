from __future__ import annotations

import os
import json
import smtplib
import urllib.request
from email.message import EmailMessage
from typing import Any, Dict, List

from ..db import db


VALID_CHANNELS = {"email", "whatsapp", "both"}


def _channels(preference: str) -> List[str]:
    normalized = (preference or "both").strip().lower()
    if normalized not in VALID_CHANNELS:
        normalized = "both"
    return ["email", "whatsapp"] if normalized == "both" else [normalized]


def _send_channel(channel: str, user: Dict[str, Any], message: str, appointment_id: str) -> Dict[str, Any]:
    if channel == "email":
        configured = all(os.getenv(name, "").strip() for name in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"))
        recipient = user.get("email", "")
        if configured and recipient:
            try:
                email = EmailMessage()
                email["Subject"] = "MediGuide appointment confirmation"
                email["From"] = os.getenv("SMTP_FROM", os.getenv("SMTP_USER"))
                email["To"] = recipient
                email.set_content(message)
                with smtplib.SMTP(os.getenv("SMTP_HOST"), int(os.getenv("SMTP_PORT", "587")), timeout=8) as server:
                    server.starttls()
                    server.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD"))
                    server.send_message(email)
                status, detail = "sent", "Email accepted by the configured SMTP server."
            except Exception as exc:
                status, detail = "failed", f"SMTP delivery failed: {exc.__class__.__name__}."
        else:
            status, detail = "setup-required", "Email delivery is not configured yet. Add SMTP credentials to enable confirmations."
    else:
        configured = all(os.getenv(name, "").strip() for name in ("WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID"))
        recipient = user.get("phone", "")
        if configured and recipient:
            try:
                endpoint = f"https://graph.facebook.com/v20.0/{os.getenv('WHATSAPP_PHONE_NUMBER_ID')}/messages"
                body = json.dumps({"messaging_product": "whatsapp", "to": recipient, "type": "text", "text": {"body": message}}).encode("utf-8")
                request = urllib.request.Request(endpoint, data=body, headers={"Authorization": f"Bearer {os.getenv('WHATSAPP_ACCESS_TOKEN')}", "Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(request, timeout=8) as response:
                    status, detail = ("pending", "WhatsApp provider accepted the message; delivery status is webhook-driven.") if response.status < 300 else ("failed", "WhatsApp provider rejected the message.")
            except Exception as exc:
                status, detail = "failed", f"WhatsApp delivery failed: {exc.__class__.__name__}."
        else:
            status, detail = "setup-required", "WhatsApp delivery is not configured yet. Add Cloud API credentials to enable messages."
    return {"channel": channel, "recipient": recipient, "status": status, "detail": detail, "message": message, "appointmentId": appointment_id}


def notify_appointment(user: Dict[str, Any], appointment: Dict[str, Any]) -> List[Dict[str, Any]]:
    prefix = "MediGuide appointment details are awaiting patient OTP/CAPTCHA verification" if appointment.get("status") == "awaiting-patient-verification" else "MediGuide appointment confirmed"
    message = f"{prefix}: {appointment.get('specialistName')} on {appointment.get('date')} at {appointment.get('time')}. Reference: {appointment.get('bookingReference')}."
    return [db.create_notification(user["id"], _send_channel(channel, user, message, appointment["id"])) for channel in _channels(user.get("preferredContact", "both"))]


def notify_cancellation(user: Dict[str, Any], appointment: Dict[str, Any]) -> List[Dict[str, Any]]:
    message = f"MediGuide appointment cancelled: {appointment.get('specialistName')} on {appointment.get('date')} at {appointment.get('time')}. Reference: {appointment.get('bookingReference')}."
    return [db.create_notification(user["id"], _send_channel(channel, user, message, appointment["id"])) for channel in _channels(user.get("preferredContact", "both"))]


def apply_delivery_status(notification_id: str, status: str) -> Dict[str, Any] | None:
    if status not in {"pending", "delivered", "read", "failed"}:
        return None
    return db.update_notification_status(notification_id, status)
