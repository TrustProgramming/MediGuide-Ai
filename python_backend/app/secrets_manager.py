"""Application secret resolution.

The signing key protects two things: the session tokens that authenticate every
request, and the AES key that encrypts patients' medical records. A key that is
committed in source is not a secret, so a hardcoded fallback is never used.

Resolution order:

1. ``MEDIGUIDE_SECRET`` from the environment - the only option for production.
2. A machine-local key file, generated once with ``secrets.token_urlsafe``.

The generated file keeps local development working across restarts (sessions and
encrypted records survive) without shipping a known key. It lives in the data
directory and is git-ignored.
"""

from __future__ import annotations

import logging
import os
import secrets
import stat
from pathlib import Path

from .config import DATA_DIR

logger = logging.getLogger(__name__)

KEY_FILE = DATA_DIR / ".mediguide-secret"
MIN_LENGTH = 32

# Rejected outright: these shipped as defaults in earlier versions.
KNOWN_WEAK = {
    "local-mediguide-development-secret",
    "mediguide-python-development-secret",
    "change-me",
    "secret",
    "replace-with-a-long-random-secret",
}


def _generate_local_key() -> str:
    """Create and persist a random development key, readable only by this user."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    value = secrets.token_urlsafe(48)
    KEY_FILE.write_text(value, encoding="utf-8")
    try:
        KEY_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:  # pragma: no cover - platform dependent
        logger.debug("Could not restrict permissions on the local key file")
    logger.warning(
        "MEDIGUIDE_SECRET was not set. Generated a random development key at %s. "
        "Set MEDIGUIDE_SECRET in the environment before deploying.",
        KEY_FILE,
    )
    return value


def resolve_secret() -> str:
    """Return the application secret, never a hardcoded default."""
    configured = os.getenv("MEDIGUIDE_SECRET", "").strip()
    if configured:
        if configured in KNOWN_WEAK:
            raise RuntimeError(
                "MEDIGUIDE_SECRET is set to a known placeholder value. "
                "Generate a real one, for example: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
            )
        if len(configured) < MIN_LENGTH:
            raise RuntimeError(
                f"MEDIGUIDE_SECRET must be at least {MIN_LENGTH} characters; it signs session "
                "tokens and derives the medical-record encryption key."
            )
        return configured

    if KEY_FILE.exists():
        stored = KEY_FILE.read_text(encoding="utf-8").strip()
        if stored and len(stored) >= MIN_LENGTH:
            return stored

    return _generate_local_key()


def secret_source() -> str:
    """Where the current secret came from. For diagnostics; never the value."""
    return "environment" if os.getenv("MEDIGUIDE_SECRET", "").strip() else "generated-local-file"
