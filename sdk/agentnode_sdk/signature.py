"""Publisher signature verification — Phase 16.1 foundation.

Provides Ed25519 signature verification over the canonical payload.
Does NOT include key generation or signing — those are Phase 16.2+.

Key invariant: The signature payload is slug + Phase 15 v1 canonical
fields.  It NEVER includes _signatures (would be circular — the
signature cannot sign itself).

The _integrity v2 hash DOES include _signatures, which protects
against signature/public-key swap attacks.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from enum import Enum

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


SIGN_ALGORITHM = "ed25519"


class SignatureStatus(str, Enum):
    """Possible outcomes of publisher signature verification."""

    VALID = "valid"
    MISSING = "missing"
    INVALID = "invalid"
    REVOKED = "revoked"
    UNKNOWN_KEY = "unknown_key"


@dataclass
class SignatureResult:
    """Result of verifying a lockfile entry's publisher signature."""

    status: SignatureStatus
    slug: str
    key_id: str | None = None
    error: str | None = None


def build_sign_payload(slug: str, entry: dict) -> bytes:
    """Build the canonical payload for publisher signing.

    Uses Phase 15 v1 canonical fields + slug.
    NEVER includes ``_signatures`` — the signature cannot sign itself.
    """
    from agentnode_sdk.lock_integrity import _build_canonical

    canonical = _build_canonical(entry, canonical_version=1)
    payload_dict = {"slug": slug, **canonical}
    return json.dumps(
        payload_dict,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def verify_signature(
    payload: bytes,
    signature_bytes: bytes,
    public_key_bytes: bytes,
) -> bool:
    """Verify an Ed25519 signature over *payload*.

    Returns ``True`` if the signature is valid, ``False`` otherwise.
    """
    try:
        public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
        public_key.verify(signature_bytes, payload)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def _extract_publisher_signature(entry: dict) -> dict | None:
    """Extract the first publisher signature from a lockfile entry.

    Handles both array format ``[{...}]`` and legacy dict format ``{...}``.
    """
    sigs = entry.get("_signatures")
    if not sigs or not isinstance(sigs, dict):
        return None
    publisher = sigs.get("publisher")
    if not publisher:
        return None
    if isinstance(publisher, list):
        return publisher[0] if publisher else None
    if isinstance(publisher, dict):
        return publisher
    return None


def verify_entry_signature(slug: str, entry: dict) -> SignatureResult:
    """Verify a lockfile entry's publisher signature.

    Returns a :class:`SignatureResult` with the verification outcome.
    Does NOT check revocation status (requires registry call).
    """
    sig_data = _extract_publisher_signature(entry)
    if sig_data is None:
        return SignatureResult(status=SignatureStatus.MISSING, slug=slug)

    key_id = sig_data.get("key_id")
    algorithm = sig_data.get("algorithm", "")
    sig_b64 = sig_data.get("signature", "")
    pub_b64 = sig_data.get("public_key", "")

    if algorithm != SIGN_ALGORITHM:
        return SignatureResult(
            status=SignatureStatus.INVALID,
            slug=slug,
            key_id=key_id,
            error=f"Unsupported algorithm: {algorithm}",
        )

    try:
        signature_bytes = base64.b64decode(sig_b64)
        public_key_bytes = base64.b64decode(pub_b64)
    except Exception:
        return SignatureResult(
            status=SignatureStatus.INVALID,
            slug=slug,
            key_id=key_id,
            error="Malformed signature or public key encoding",
        )

    payload = build_sign_payload(slug, entry)

    if verify_signature(payload, signature_bytes, public_key_bytes):
        return SignatureResult(
            status=SignatureStatus.VALID,
            slug=slug,
            key_id=key_id,
        )

    return SignatureResult(
        status=SignatureStatus.INVALID,
        slug=slug,
        key_id=key_id,
        error="Signature verification failed",
    )
