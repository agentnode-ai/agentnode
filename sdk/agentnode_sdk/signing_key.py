"""Publisher signing key management — Phase 16.2.

Local Ed25519 key generation, storage, and signing.
Private key stored as PEM (PKCS8) at ~/.agentnode/signing_key.
"""
from __future__ import annotations

import hashlib
import logging
import os
import stat
import sys
import warnings
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

logger = logging.getLogger(__name__)

SIGNING_KEY_FILENAME = "signing_key"


def generate_ed25519_keypair() -> tuple[Ed25519PrivateKey, bytes]:
    """Generate a new Ed25519 keypair.

    Returns (private_key, public_key_raw_bytes).
    """
    private_key = Ed25519PrivateKey.generate()
    public_key_bytes = private_key.public_key().public_bytes(
        Encoding.Raw, PublicFormat.Raw,
    )
    return private_key, public_key_bytes


def sign_payload(payload: bytes, private_key: Ed25519PrivateKey) -> bytes:
    """Sign *payload* with an Ed25519 private key. Returns 64-byte signature."""
    return private_key.sign(payload)


def compute_key_id(public_key_bytes: bytes) -> str:
    """Derive a key_id from raw public key bytes.

    Format: ``ed25519:{sha256_fingerprint_first_16_hex}``.
    """
    fingerprint = hashlib.sha256(public_key_bytes).hexdigest()[:16]
    return f"ed25519:{fingerprint}"


def get_signing_key_path() -> Path:
    """Return the default signing key path (~/.agentnode/signing_key)."""
    from agentnode_sdk.config import config_dir
    return config_dir() / SIGNING_KEY_FILENAME


def save_signing_key(
    private_key: Ed25519PrivateKey,
    path: Path | None = None,
) -> Path:
    """Save an Ed25519 private key to *path* as PEM (PKCS8, unencrypted).

    Creates parent directories if needed. Sets 0600 permissions on POSIX.
    """
    if path is None:
        path = get_signing_key_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    pem_bytes = private_key.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption(),
    )
    path.write_bytes(pem_bytes)

    if sys.platform != "win32":
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600

    return path


def load_signing_key(path: Path | None = None) -> Ed25519PrivateKey:
    """Load an Ed25519 private key from PEM file at *path*.

    Warns if file permissions are too open on POSIX.
    Raises ``FileNotFoundError`` if the key file does not exist.
    Raises ``ValueError`` if the file is not a valid Ed25519 PEM key.
    """
    if path is None:
        path = get_signing_key_path()

    if not path.is_file():
        raise FileNotFoundError(f"Signing key not found: {path}")

    _check_key_permissions(path)

    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    pem_bytes = path.read_bytes()
    try:
        key = load_pem_private_key(pem_bytes, password=None)
    except Exception as exc:
        raise ValueError(f"Invalid signing key at {path}: {exc}") from exc

    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError(
            f"Expected Ed25519 private key, got {type(key).__name__}"
        )
    return key


def get_or_create_signing_key(
    path: Path | None = None,
) -> Ed25519PrivateKey:
    """Load the signing key, or generate and save a new one if absent."""
    if path is None:
        path = get_signing_key_path()

    if path.is_file():
        return load_signing_key(path)

    private_key, _pub = generate_ed25519_keypair()
    save_signing_key(private_key, path)
    logger.info("Generated new signing key at %s", path)
    return private_key


def _check_key_permissions(path: Path) -> None:
    """Warn if the signing key file has too-open permissions.

    On POSIX: warns if group or other bits are set.
    On Windows: no reliable POSIX permission check — skip silently.
    """
    if sys.platform == "win32":
        return

    try:
        mode = path.stat().st_mode
        if mode & (stat.S_IRGRP | stat.S_IWGRP | stat.S_IXGRP |
                    stat.S_IROTH | stat.S_IWOTH | stat.S_IXOTH):
            warnings.warn(
                f"Signing key {path} has too-open permissions "
                f"(mode {oct(mode & 0o777)}). "
                f"Recommended: chmod 600 {path}",
                stacklevel=3,
            )
    except OSError:
        pass
