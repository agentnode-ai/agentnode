"""Encryption layer for credential storage — Fernet/AES-256.

Security invariants (S12):
- Key derived from CREDENTIAL_ENCRYPTION_KEY env var
- Never log raw secrets or encryption keys
- Encryption errors fail closed (raise, never return partial data)
"""
from __future__ import annotations

import json
import logging
import os

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("agentnode.credentials.vault")


def _get_encryption_key() -> bytes:
    """Load the Fernet key from environment.

    In production this MUST be a 32-byte URL-safe base64-encoded key.
    Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    """
    key = os.environ.get("CREDENTIAL_ENCRYPTION_KEY", "")
    if not key:
        raise RuntimeError(
            "CREDENTIAL_ENCRYPTION_KEY is not set. "
            "Credential storage requires an encryption key."
        )
    return key.encode("utf-8")


def encrypt(data: dict) -> str:
    """Encrypt a dict as Fernet-encrypted string.

    The dict is serialized to JSON, then encrypted. Returns a UTF-8 string
    suitable for storing in a Text column.
    """
    key = _get_encryption_key()
    f = Fernet(key)
    plaintext = json.dumps(data, ensure_ascii=False).encode("utf-8")
    return f.encrypt(plaintext).decode("utf-8")


def decrypt(encrypted: str) -> dict:
    """Decrypt a Fernet-encrypted string back to a dict.

    Raises RuntimeError on invalid key or tampered data (fail closed).
    """
    key = _get_encryption_key()
    f = Fernet(key)
    try:
        plaintext = f.decrypt(encrypted.encode("utf-8"))
    except InvalidToken:
        raise RuntimeError(
            "Failed to decrypt credential data. "
            "The encryption key may have changed or the data is corrupted."
        )
    return json.loads(plaintext)
