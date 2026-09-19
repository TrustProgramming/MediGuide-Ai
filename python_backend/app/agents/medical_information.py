from __future__ import annotations

import base64
import hashlib
import os
from typing import Any, Dict

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..config import SECRET_KEY


# Same resolved secret as session signing, so records stay readable across
# restarts and no known key is ever used to encrypt patient data.
_KEY = hashlib.sha256(SECRET_KEY.encode("utf-8")).digest()


def encrypt_details(details: str) -> Dict[str, str]:
    nonce = os.urandom(12)
    ciphertext = AESGCM(_KEY).encrypt(nonce, details.encode("utf-8"), None)
    return {"encryptedDetails": base64.urlsafe_b64encode(ciphertext).decode("ascii"), "nonce": base64.urlsafe_b64encode(nonce).decode("ascii"), "encryption": "AES-256-GCM"}


def decrypt_details(record: Dict[str, Any]) -> str:
    try:
        nonce = base64.urlsafe_b64decode(record["nonce"])
        ciphertext = base64.urlsafe_b64decode(record["encryptedDetails"])
        return AESGCM(_KEY).decrypt(nonce, ciphertext, None).decode("utf-8")
    except Exception:
        return ""
