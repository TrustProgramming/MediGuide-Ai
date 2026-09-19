import hashlib
import hmac
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import jwt

from .config import SECRET_KEY
from .db import db


def hash_password(password: str, salt: Optional[str] = None) -> str:
    salt = salt or __import__("secrets").token_hex(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt.encode("utf-8"), n=16384, r=8, p=1, dklen=64)
    return f"{salt}:{digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, expected = stored.split(":", 1)
        digest = hashlib.scrypt(password.encode("utf-8"), salt=salt.encode("utf-8"), n=16384, r=8, p=1, dklen=64)
        return hmac.compare_digest(digest.hex(), expected)
    except Exception:
        return False


def token_for(user_id: str) -> str:
    payload = {"sub": user_id, "exp": datetime.utcnow() + timedelta(days=7)}
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def user_from_token(token: str) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        return db.find_user_by_id(payload.get("sub"))
    except Exception:
        return None


def public_patient(user: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": user.get("id"),
        "fullName": user.get("fullName"),
        "email": user.get("email"),
        "phone": user.get("phone") or "",
        "city": user.get("city") or "Lahore",
        "preferredContact": user.get("preferredContact") or "both",
        "profilePicture": user.get("profilePicture") or "",
    }


def auth_response(user: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "accessToken": token_for(user["id"]),
        "tokenType": "bearer",
        "patient": public_patient(user),
    }
