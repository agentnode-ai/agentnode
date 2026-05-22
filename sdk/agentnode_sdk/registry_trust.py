"""Registry response authenticity — TG-4.

Verifies that registry API responses are signed by a known registry key.
Closes the first-install trust bootstrap gap: the SDK no longer trusts
registry metadata based solely on TLS transport.

The registry signs the final serialized JSON response body bytes of
trust-critical endpoints. The SDK verifies against httpx.Response.content
(decompressed, fully reconstructed). No canonicalization — exact bytes.
"""
from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


SIGNATURE_HEADER = "X-AgentNode-Signature"

_MAX_HEADER_LENGTH = 8192
_ED25519_SIG_LENGTH = 64
_ED25519_KEY_LENGTH = 32
_KEY_ID_RE = re.compile(r"^[a-z0-9._-]{1,64}$")
_ALLOWED_ALGORITHMS = frozenset({"ed25519"})


@dataclass(frozen=True)
class RegistryKey:
    key_id: str
    algorithm: str
    public_key: str
    not_after: str | None = None


# SECURITY INVARIANT: REGISTRY_KEYS are compile-time trust anchors.
# They MUST NOT be modified at runtime — no env override, no config
# file, no network fetch, no dynamic refresh. Mutating this mapping
# would allow an attacker to inject a trusted key via any writable
# channel, completely bypassing TG-4.
#
# MappingProxyType enforces immutability at runtime — any attempt
# to assign, delete, or update raises TypeError.
REGISTRY_KEYS: MappingProxyType = MappingProxyType({
    # Empty during bootstrap phase — no enforcement.
    # Once populated, MISSING header on trust-critical endpoints
    # becomes a hard deny (prevents downgrade attack).
})


class RegistrySignatureStatus(str, Enum):
    VALID = "valid"
    BOOTSTRAP = "bootstrap"
    MISSING = "missing"
    INVALID = "invalid"
    UNKNOWN_KEY = "unknown_key"
    EXPIRED_KEY = "expired_key"


@dataclass
class RegistrySignatureResult:
    status: RegistrySignatureStatus
    key_id: str | None = None
    error: str | None = None


# --- Endpoint matching ---

_TRUST_CRITICAL_PATTERNS = (
    re.compile(r"^(/v1)?/packages/[^/]+$"),
    re.compile(r"^(/v1)?/packages/[^/]+/install-info$"),
    re.compile(r"^(/v1)?/publishers/[^/]+/keys/[^/]+$"),
)


def is_trust_critical(path: str) -> bool:
    """Match trust-critical endpoints against normalized path.

    SECURITY CRITICAL: Incorrect matching here disables registry
    authenticity verification for trust-critical endpoints. An
    unmatched endpoint receives no signature check.

    Normalization: trailing slash stripped. This is the only
    normalization applied — no URL decoding, no case folding.
    """
    normalized = path.rstrip("/")
    return any(p.match(normalized) for p in _TRUST_CRITICAL_PATTERNS)


def enforcement_active() -> bool:
    """Whether registry signature enforcement is active.

    True when at least one registry key is pinned.
    When False, all responses get BOOTSTRAP status (observational).
    When True, missing signatures get MISSING status (deny).
    """
    return bool(REGISTRY_KEYS)


# --- Header parsing ---

def parse_signature_header(header: str) -> tuple[str, str, bytes]:
    """Parse X-AgentNode-Signature header.

    Format: ``algorithm:key_id:base64_signature``

    Returns (algorithm, key_id, signature_bytes).
    Raises ValueError on any validation failure.
    """
    if len(header) > _MAX_HEADER_LENGTH:
        raise ValueError(f"Signature header exceeds {_MAX_HEADER_LENGTH} bytes")
    # split(":", 2) is correct because base64 can contain "=" but never ":".
    parts = header.split(":", 2)
    if len(parts) != 3:
        raise ValueError("Signature header must be algorithm:key_id:base64sig")
    algorithm, key_id, sig_b64 = parts
    if algorithm not in _ALLOWED_ALGORITHMS:
        raise ValueError(f"Unsupported algorithm: {algorithm}")
    if not _KEY_ID_RE.match(key_id):
        raise ValueError(f"Invalid key_id format: {key_id!r}")
    try:
        sig_bytes = base64.b64decode(sig_b64, validate=True)
    except binascii.Error as exc:
        raise ValueError(f"Invalid base64 in signature: {exc}") from exc
    if len(sig_bytes) != _ED25519_SIG_LENGTH:
        raise ValueError(
            f"Signature must be {_ED25519_SIG_LENGTH} bytes, got {len(sig_bytes)}"
        )
    return algorithm, key_id, sig_bytes


# --- Verification ---

def verify_registry_response(
    response_body: bytes,
    signature_header: str | None,
) -> RegistrySignatureResult:
    """Verify a registry response's authenticity.

    Returns a result with clean semantics:
    - BOOTSTRAP: no pinned keys, feature inactive (allow)
    - VALID: signature verified against pinned key (allow)
    - MISSING: enforcement active, header stripped (deny — downgrade)
    - INVALID: known key, bad signature (deny — tampering)
    - UNKNOWN_KEY: unrecognized key_id (deny — SDK outdated)
    - EXPIRED_KEY: key past not_after (deny — SDK outdated)
    """
    if not REGISTRY_KEYS:
        return RegistrySignatureResult(status=RegistrySignatureStatus.BOOTSTRAP)

    if signature_header is None:
        return RegistrySignatureResult(
            status=RegistrySignatureStatus.MISSING,
            error="Registry keys are pinned but response has no signature",
        )

    try:
        algorithm, key_id, sig_bytes = parse_signature_header(signature_header)
    except ValueError as exc:
        return RegistrySignatureResult(
            status=RegistrySignatureStatus.INVALID,
            error=str(exc),
        )

    key_entry = REGISTRY_KEYS.get(key_id)
    if key_entry is None:
        return RegistrySignatureResult(
            status=RegistrySignatureStatus.UNKNOWN_KEY,
            key_id=key_id,
            error=f"Unknown registry key: {key_id}",
        )

    not_after = key_entry.not_after
    if not_after:
        from datetime import date
        if date.today() > date.fromisoformat(not_after):
            return RegistrySignatureResult(
                status=RegistrySignatureStatus.EXPIRED_KEY,
                key_id=key_id,
                error=f"Registry key expired: {not_after}",
            )

    if algorithm != key_entry.algorithm:
        return RegistrySignatureResult(
            status=RegistrySignatureStatus.INVALID,
            key_id=key_id,
            error=f"Algorithm mismatch: {algorithm} != {key_entry.algorithm}",
        )

    try:
        pub_bytes = base64.b64decode(key_entry.public_key, validate=True)
    except binascii.Error as exc:
        return RegistrySignatureResult(
            status=RegistrySignatureStatus.INVALID,
            key_id=key_id,
            error=f"Invalid base64 in pinned public key: {exc}",
        )
    if len(pub_bytes) != _ED25519_KEY_LENGTH:
        return RegistrySignatureResult(
            status=RegistrySignatureStatus.INVALID,
            key_id=key_id,
            error=f"Pinned key must be {_ED25519_KEY_LENGTH} bytes, got {len(pub_bytes)}",
        )

    try:
        public_key = Ed25519PublicKey.from_public_bytes(pub_bytes)
        public_key.verify(sig_bytes, response_body)
        return RegistrySignatureResult(
            status=RegistrySignatureStatus.VALID,
            key_id=key_id,
        )
    except (InvalidSignature, ValueError, TypeError):
        return RegistrySignatureResult(
            status=RegistrySignatureStatus.INVALID,
            key_id=key_id,
            error="Signature verification failed",
        )
