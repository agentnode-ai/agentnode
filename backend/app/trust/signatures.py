"""Ed25519 signature verification for ANP packages.

Publishers sign their artifact hash with their Ed25519 private key.
AgentNode stores the publisher's public key and verifies signatures
during install and trust evaluation.

Flow:
  1. Publisher generates Ed25519 keypair: `agentnode keygen`
  2. Publisher registers public key via API
  3. On publish, publisher signs the SHA256 artifact hash
  4. Backend verifies signature using stored public key
  5. CLI verifies again on install
"""
from __future__ import annotations

import base64
import hashlib
import logging

logger = logging.getLogger(__name__)


def verify_signature(
    public_key_b64: str,
    signature_b64: str,
    artifact_hash_hex: str,
) -> bool:
    """Verify an Ed25519 signature over an artifact hash.

    Args:
        public_key_b64: Base64-encoded Ed25519 public key (32 bytes)
        signature_b64: Base64-encoded Ed25519 signature (64 bytes)
        artifact_hash_hex: Hex-encoded SHA256 hash of the artifact

    Returns:
        True if signature is valid, False otherwise.
    """
    try:
        from nacl.signing import VerifyKey
        from nacl.exceptions import BadSignatureError

        public_key_bytes = base64.b64decode(public_key_b64)
        signature_bytes = base64.b64decode(signature_b64)
        message = artifact_hash_hex.encode("utf-8")

        verify_key = VerifyKey(public_key_bytes)
        verify_key.verify(message, signature_bytes)
        return True
    except (BadSignatureError, Exception) as e:
        logger.warning(f"Signature verification failed: {e}")
        return False


def sign_artifact_hash(
    private_key_b64: str,
    artifact_hash_hex: str,
) -> str:
    """Sign an artifact hash with an Ed25519 private key.

    Args:
        private_key_b64: Base64-encoded Ed25519 signing key (32 bytes seed)
        artifact_hash_hex: Hex-encoded SHA256 hash of the artifact

    Returns:
        Base64-encoded signature string.
    """
    from nacl.signing import SigningKey

    private_key_bytes = base64.b64decode(private_key_b64)
    signing_key = SigningKey(private_key_bytes)
    message = artifact_hash_hex.encode("utf-8")
    signed = signing_key.sign(message)
    return base64.b64encode(signed.signature).decode("utf-8")


def generate_keypair() -> tuple[str, str]:
    """Generate a new Ed25519 keypair.

    Returns:
        (private_key_b64, public_key_b64) tuple.
    """
    from nacl.signing import SigningKey

    signing_key = SigningKey.generate()
    private_b64 = base64.b64encode(bytes(signing_key)).decode("utf-8")
    public_b64 = base64.b64encode(bytes(signing_key.verify_key)).decode("utf-8")
    return private_b64, public_b64
