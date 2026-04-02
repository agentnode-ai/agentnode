"""Ed25519 signature verification tests — Audit item Testing P0.2.

Covers:
  - Valid signature verifies successfully
  - Invalid / corrupted signature is rejected
  - Tampered artifact hash fails verification
  - Wrong public key fails verification
  - Missing signature field handling
  - Empty signature handling
  - Round-trip: generate_keypair -> sign_artifact_hash -> verify_signature
  - Malformed base64 inputs
"""

import base64
import hashlib

import pytest
from nacl.signing import SigningKey

from app.trust.signatures import generate_keypair, sign_artifact_hash, verify_signature


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_keypair() -> tuple[str, str, SigningKey]:
    """Return (private_b64, public_b64, SigningKey) for test use."""
    sk = SigningKey.generate()
    priv_b64 = base64.b64encode(bytes(sk)).decode()
    pub_b64 = base64.b64encode(bytes(sk.verify_key)).decode()
    return priv_b64, pub_b64, sk


def _artifact_hash(content: bytes = b"hello world") -> str:
    return hashlib.sha256(content).hexdigest()


# ---------------------------------------------------------------------------
# Valid signature
# ---------------------------------------------------------------------------


class TestValidSignature:

    def test_round_trip_sign_and_verify(self):
        """A signature produced by sign_artifact_hash verifies with the matching public key."""
        priv_b64, pub_b64, _ = _make_keypair()
        artifact_hash = _artifact_hash()

        signature_b64 = sign_artifact_hash(priv_b64, artifact_hash)
        assert verify_signature(pub_b64, signature_b64, artifact_hash) is True

    def test_generate_keypair_round_trip(self):
        """generate_keypair() keys work end-to-end."""
        priv_b64, pub_b64 = generate_keypair()
        artifact_hash = _artifact_hash(b"some artifact content")

        sig = sign_artifact_hash(priv_b64, artifact_hash)
        assert verify_signature(pub_b64, sig, artifact_hash) is True

    def test_different_artifacts_produce_different_signatures(self):
        """Signing two different hashes must yield distinct signatures."""
        priv_b64, pub_b64, _ = _make_keypair()
        hash_a = _artifact_hash(b"artifact-a")
        hash_b = _artifact_hash(b"artifact-b")

        sig_a = sign_artifact_hash(priv_b64, hash_a)
        sig_b = sign_artifact_hash(priv_b64, hash_b)
        assert sig_a != sig_b

    def test_signature_is_base64_encoded(self):
        """sign_artifact_hash returns valid base64."""
        priv_b64, _, _ = _make_keypair()
        sig = sign_artifact_hash(priv_b64, _artifact_hash())
        raw = base64.b64decode(sig)
        assert len(raw) == 64  # Ed25519 signature is 64 bytes


# ---------------------------------------------------------------------------
# Invalid signature
# ---------------------------------------------------------------------------


class TestInvalidSignature:

    def test_corrupted_signature_rejected(self):
        """Flipping a byte in the signature must fail verification."""
        priv_b64, pub_b64, _ = _make_keypair()
        artifact_hash = _artifact_hash()
        sig_b64 = sign_artifact_hash(priv_b64, artifact_hash)

        # Corrupt one byte
        sig_bytes = bytearray(base64.b64decode(sig_b64))
        sig_bytes[0] ^= 0xFF
        bad_sig = base64.b64encode(bytes(sig_bytes)).decode()

        assert verify_signature(pub_b64, bad_sig, artifact_hash) is False

    def test_completely_random_signature_rejected(self):
        """A random 64-byte signature must fail."""
        import os
        _, pub_b64, _ = _make_keypair()
        random_sig = base64.b64encode(os.urandom(64)).decode()
        assert verify_signature(pub_b64, random_sig, _artifact_hash()) is False

    def test_truncated_signature_rejected(self):
        """A signature shorter than 64 bytes must fail."""
        priv_b64, pub_b64, _ = _make_keypair()
        artifact_hash = _artifact_hash()
        sig_b64 = sign_artifact_hash(priv_b64, artifact_hash)

        # Truncate to 32 bytes
        sig_bytes = base64.b64decode(sig_b64)[:32]
        short_sig = base64.b64encode(sig_bytes).decode()

        assert verify_signature(pub_b64, short_sig, artifact_hash) is False


# ---------------------------------------------------------------------------
# Tampered content
# ---------------------------------------------------------------------------


class TestTamperedContent:

    def test_different_artifact_hash_fails(self):
        """Signature for hash A must not verify against hash B."""
        priv_b64, pub_b64, _ = _make_keypair()
        hash_a = _artifact_hash(b"original content")
        hash_b = _artifact_hash(b"tampered content")

        sig = sign_artifact_hash(priv_b64, hash_a)
        assert verify_signature(pub_b64, sig, hash_a) is True
        assert verify_signature(pub_b64, sig, hash_b) is False

    def test_one_bit_change_in_hash_fails(self):
        """Even a single-character change in the hash hex string fails."""
        priv_b64, pub_b64, _ = _make_keypair()
        artifact_hash = _artifact_hash()
        sig = sign_artifact_hash(priv_b64, artifact_hash)

        # Flip the last hex digit
        last_char = artifact_hash[-1]
        flipped = "0" if last_char != "0" else "1"
        tampered_hash = artifact_hash[:-1] + flipped

        assert verify_signature(pub_b64, sig, tampered_hash) is False


# ---------------------------------------------------------------------------
# Wrong public key
# ---------------------------------------------------------------------------


class TestWrongPublicKey:

    def test_wrong_key_rejects_valid_signature(self):
        """A valid signature must fail when verified with a different public key."""
        priv_a, pub_a, _ = _make_keypair()
        _, pub_b, _ = _make_keypair()

        artifact_hash = _artifact_hash()
        sig = sign_artifact_hash(priv_a, artifact_hash)

        assert verify_signature(pub_a, sig, artifact_hash) is True
        assert verify_signature(pub_b, sig, artifact_hash) is False

    def test_swapped_keys_between_publishers(self):
        """Publisher A's signature must not verify with Publisher B's key and vice versa."""
        priv_a, pub_a, _ = _make_keypair()
        priv_b, pub_b, _ = _make_keypair()
        artifact_hash = _artifact_hash()

        sig_a = sign_artifact_hash(priv_a, artifact_hash)
        sig_b = sign_artifact_hash(priv_b, artifact_hash)

        # Each signature only works with its own key
        assert verify_signature(pub_a, sig_a, artifact_hash) is True
        assert verify_signature(pub_b, sig_b, artifact_hash) is True
        assert verify_signature(pub_a, sig_b, artifact_hash) is False
        assert verify_signature(pub_b, sig_a, artifact_hash) is False


# ---------------------------------------------------------------------------
# Missing signature field
# ---------------------------------------------------------------------------


class TestMissingSignature:

    def test_empty_string_signature(self):
        """An empty string signature must fail (not crash)."""
        _, pub_b64, _ = _make_keypair()
        result = verify_signature(pub_b64, "", _artifact_hash())
        assert result is False

    def test_empty_string_public_key(self):
        """An empty string public key must fail (not crash)."""
        priv_b64, _, _ = _make_keypair()
        sig = sign_artifact_hash(priv_b64, _artifact_hash())
        result = verify_signature("", sig, _artifact_hash())
        assert result is False

    def test_empty_string_artifact_hash(self):
        """An empty artifact hash must fail (signature won't match)."""
        priv_b64, pub_b64, _ = _make_keypair()
        artifact_hash = _artifact_hash()
        sig = sign_artifact_hash(priv_b64, artifact_hash)

        # Verify against empty hash should fail
        assert verify_signature(pub_b64, sig, "") is False


# ---------------------------------------------------------------------------
# Malformed base64 inputs
# ---------------------------------------------------------------------------


class TestMalformedInputs:

    def test_invalid_base64_signature(self):
        """Non-base64 signature string must return False, not raise."""
        _, pub_b64, _ = _make_keypair()
        result = verify_signature(pub_b64, "not!valid!base64!!!", _artifact_hash())
        assert result is False

    def test_invalid_base64_public_key(self):
        """Non-base64 public key must return False, not raise."""
        priv_b64, _, _ = _make_keypair()
        sig = sign_artifact_hash(priv_b64, _artifact_hash())
        result = verify_signature("not!valid!base64!!!", sig, _artifact_hash())
        assert result is False

    def test_wrong_length_public_key(self):
        """A key that is valid base64 but wrong length must return False."""
        priv_b64, _, _ = _make_keypair()
        sig = sign_artifact_hash(priv_b64, _artifact_hash())
        # 16 bytes instead of 32
        short_key = base64.b64encode(b"\x00" * 16).decode()
        result = verify_signature(short_key, sig, _artifact_hash())
        assert result is False

    def test_wrong_length_signature(self):
        """Signature with wrong byte length must return False."""
        _, pub_b64, _ = _make_keypair()
        # 32 bytes instead of 64
        short_sig = base64.b64encode(b"\x00" * 32).decode()
        result = verify_signature(pub_b64, short_sig, _artifact_hash())
        assert result is False


# ---------------------------------------------------------------------------
# generate_keypair correctness
# ---------------------------------------------------------------------------


class TestGenerateKeypair:

    def test_returns_valid_base64_strings(self):
        """Both returned keys must be valid base64."""
        priv_b64, pub_b64 = generate_keypair()
        priv_bytes = base64.b64decode(priv_b64)
        pub_bytes = base64.b64decode(pub_b64)
        assert len(priv_bytes) == 32  # Ed25519 seed
        assert len(pub_bytes) == 32  # Ed25519 public key

    def test_unique_keys_each_call(self):
        """Each call must produce a distinct keypair."""
        pair_a = generate_keypair()
        pair_b = generate_keypair()
        assert pair_a[0] != pair_b[0]  # Different private keys
        assert pair_a[1] != pair_b[1]  # Different public keys

    def test_private_and_public_differ(self):
        """Private and public keys must not be the same."""
        priv_b64, pub_b64 = generate_keypair()
        assert priv_b64 != pub_b64
