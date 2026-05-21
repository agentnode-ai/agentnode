"""Tests for publisher signature verification — Phase 16.1."""
import base64
import hashlib
import json

import pytest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from agentnode_sdk.signature import (
    SIGN_ALGORITHM,
    SignatureResult,
    SignatureStatus,
    build_sign_payload,
    verify_entry_signature,
    verify_signature,
    _extract_publisher_signature,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_entry(**overrides) -> dict:
    entry = {
        "version": "1.0.0",
        "package_type": "toolpack",
        "runtime": "python",
        "entrypoint": "my_pack.tool",
        "artifact_hash": "sha256:abc123def456",
        "tools": [
            {"name": "do_thing", "entrypoint": "my_pack.tool:do_thing", "action_type": "read"}
        ],
        "permissions": {
            "network_level": "none",
            "filesystem_level": "none",
        },
        "mcp_command": None,
        "remote_endpoint": None,
        "connector": None,
        "agent": None,
        "prompts": [],
        "resources": [],
        "assets": None,
        "installed_at": "2026-05-21T00:00:00+00:00",
        "trust_level": "trusted",
        "source": "sdk",
        "capability_ids": [],
    }
    entry.update(overrides)
    return entry


def _generate_keypair():
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    pub_bytes = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    return private_key, pub_bytes


def _sign_entry(slug, entry, private_key, public_key_bytes):
    """Create an entry with a valid publisher signature."""
    payload = build_sign_payload(slug, entry)
    signature = private_key.sign(payload)
    fingerprint = hashlib.sha256(public_key_bytes).hexdigest()[:16]
    signed = dict(entry)
    signed["_signatures"] = {
        "publisher": [{
            "key_id": f"ed25519:{fingerprint}",
            "algorithm": "ed25519",
            "signature": base64.b64encode(signature).decode(),
            "public_key": base64.b64encode(public_key_bytes).decode(),
            "canonical_version": 1,
            "signed_at": "2026-05-21T12:00:00+00:00",
        }],
    }
    return signed


# ---------------------------------------------------------------------------
# build_sign_payload
# ---------------------------------------------------------------------------

class TestBuildSignPayload:
    def test_deterministic(self):
        entry = _make_entry()
        p1 = build_sign_payload("test-pack", entry)
        p2 = build_sign_payload("test-pack", entry)
        assert p1 == p2

    def test_includes_slug(self):
        entry = _make_entry()
        payload = build_sign_payload("my-slug", entry)
        parsed = json.loads(payload)
        assert parsed["slug"] == "my-slug"

    def test_different_slug_different_payload(self):
        entry = _make_entry()
        p1 = build_sign_payload("slug-a", entry)
        p2 = build_sign_payload("slug-b", entry)
        assert p1 != p2

    def test_uses_v1_canonical_fields(self):
        from agentnode_sdk.lock_integrity import CANONICAL_FIELDS
        entry = _make_entry()
        payload = build_sign_payload("test-pack", entry)
        parsed = json.loads(payload)
        for f in CANONICAL_FIELDS:
            val = entry.get(f)
            if val is not None:
                assert f in parsed

    def test_excludes_signatures(self):
        """Signature payload must NEVER include _signatures."""
        entry = _make_entry()
        entry["_signatures"] = {"publisher": [{"key_id": "fake"}]}
        payload = build_sign_payload("test-pack", entry)
        parsed = json.loads(payload)
        assert "_signatures" not in parsed

    def test_excludes_integrity(self):
        entry = _make_entry()
        entry["_integrity"] = {"algorithm": "sha256", "hash": "fake"}
        payload = build_sign_payload("test-pack", entry)
        parsed = json.loads(payload)
        assert "_integrity" not in parsed

    def test_excludes_mutable_fields(self):
        entry = _make_entry()
        payload = build_sign_payload("test-pack", entry)
        parsed = json.loads(payload)
        assert "installed_at" not in parsed
        assert "trust_level" not in parsed
        assert "source" not in parsed
        assert "capability_ids" not in parsed


# ---------------------------------------------------------------------------
# verify_signature (low-level Ed25519)
# ---------------------------------------------------------------------------

class TestVerifySignature:
    def test_valid_signature(self):
        private_key, pub_bytes = _generate_keypair()
        payload = b"test payload"
        sig = private_key.sign(payload)
        assert verify_signature(payload, sig, pub_bytes) is True

    def test_tampered_payload(self):
        private_key, pub_bytes = _generate_keypair()
        payload = b"original payload"
        sig = private_key.sign(payload)
        assert verify_signature(b"tampered payload", sig, pub_bytes) is False

    def test_wrong_public_key(self):
        private_key, _ = _generate_keypair()
        _, other_pub = _generate_keypair()
        payload = b"test payload"
        sig = private_key.sign(payload)
        assert verify_signature(payload, sig, other_pub) is False

    def test_truncated_signature(self):
        _, pub_bytes = _generate_keypair()
        assert verify_signature(b"payload", b"short", pub_bytes) is False

    def test_invalid_public_key_bytes(self):
        assert verify_signature(b"payload", b"x" * 64, b"bad") is False


# ---------------------------------------------------------------------------
# _extract_publisher_signature
# ---------------------------------------------------------------------------

class TestExtractPublisherSignature:
    def test_missing_signatures_block(self):
        assert _extract_publisher_signature({}) is None

    def test_none_signatures(self):
        assert _extract_publisher_signature({"_signatures": None}) is None

    def test_empty_dict_signatures(self):
        assert _extract_publisher_signature({"_signatures": {}}) is None

    def test_empty_publisher_list(self):
        entry = {"_signatures": {"publisher": []}}
        assert _extract_publisher_signature(entry) is None

    def test_publisher_list_returns_first(self):
        sig = {"key_id": "ed25519:abc", "algorithm": "ed25519"}
        entry = {"_signatures": {"publisher": [sig, {"key_id": "second"}]}}
        assert _extract_publisher_signature(entry) == sig

    def test_publisher_dict_legacy(self):
        sig = {"key_id": "ed25519:abc", "algorithm": "ed25519"}
        entry = {"_signatures": {"publisher": sig}}
        assert _extract_publisher_signature(entry) == sig

    def test_non_dict_signatures(self):
        assert _extract_publisher_signature({"_signatures": "bad"}) is None

    def test_non_list_non_dict_publisher(self):
        assert _extract_publisher_signature({"_signatures": {"publisher": 42}}) is None


# ---------------------------------------------------------------------------
# verify_entry_signature
# ---------------------------------------------------------------------------

class TestVerifyEntrySignature:
    def test_valid_signature(self):
        private_key, pub_bytes = _generate_keypair()
        entry = _make_entry()
        signed = _sign_entry("test-pack", entry, private_key, pub_bytes)
        result = verify_entry_signature("test-pack", signed)
        assert result.status == SignatureStatus.VALID
        assert result.key_id is not None
        assert result.error is None

    def test_missing_signatures(self):
        entry = _make_entry()
        result = verify_entry_signature("test-pack", entry)
        assert result.status == SignatureStatus.MISSING
        assert result.slug == "test-pack"

    def test_tampered_entry(self):
        private_key, pub_bytes = _generate_keypair()
        entry = _make_entry()
        signed = _sign_entry("test-pack", entry, private_key, pub_bytes)
        signed["entrypoint"] = "evil.module"
        result = verify_entry_signature("test-pack", signed)
        assert result.status == SignatureStatus.INVALID
        assert "failed" in result.error

    def test_wrong_slug(self):
        """Signature for slug A must not verify under slug B."""
        private_key, pub_bytes = _generate_keypair()
        entry = _make_entry()
        signed = _sign_entry("slug-a", entry, private_key, pub_bytes)
        result = verify_entry_signature("slug-b", signed)
        assert result.status == SignatureStatus.INVALID

    def test_wrong_key(self):
        private_key, pub_bytes = _generate_keypair()
        _, other_pub = _generate_keypair()
        entry = _make_entry()
        signed = _sign_entry("test-pack", entry, private_key, pub_bytes)
        signed["_signatures"]["publisher"][0]["public_key"] = (
            base64.b64encode(other_pub).decode()
        )
        result = verify_entry_signature("test-pack", signed)
        assert result.status == SignatureStatus.INVALID

    def test_unsupported_algorithm(self):
        private_key, pub_bytes = _generate_keypair()
        entry = _make_entry()
        signed = _sign_entry("test-pack", entry, private_key, pub_bytes)
        signed["_signatures"]["publisher"][0]["algorithm"] = "rsa4096"
        result = verify_entry_signature("test-pack", signed)
        assert result.status == SignatureStatus.INVALID
        assert "Unsupported algorithm" in result.error

    def test_malformed_base64(self):
        entry = _make_entry()
        entry["_signatures"] = {
            "publisher": [{
                "key_id": "ed25519:abc",
                "algorithm": "ed25519",
                "signature": "not-valid-base64!!!",
                "public_key": "also-bad!!!",
            }],
        }
        result = verify_entry_signature("test-pack", entry)
        assert result.status == SignatureStatus.INVALID
        assert "Malformed" in result.error

    def test_mutable_field_change_preserves_validity(self):
        """Changing mutable fields must NOT invalidate the signature."""
        private_key, pub_bytes = _generate_keypair()
        entry = _make_entry()
        signed = _sign_entry("test-pack", entry, private_key, pub_bytes)
        signed["trust_level"] = "curated"
        signed["installed_at"] = "2099-01-01T00:00:00+00:00"
        signed["source"] = "cli"
        result = verify_entry_signature("test-pack", signed)
        assert result.status == SignatureStatus.VALID

    def test_signature_payload_excludes_signatures_block(self):
        """Adding _signatures to entry must not change what was signed."""
        private_key, pub_bytes = _generate_keypair()
        entry = _make_entry()
        payload_before = build_sign_payload("test-pack", entry)
        signed = _sign_entry("test-pack", entry, private_key, pub_bytes)
        payload_after = build_sign_payload("test-pack", signed)
        assert payload_before == payload_after


# ---------------------------------------------------------------------------
# SignatureStatus / SignatureResult
# ---------------------------------------------------------------------------

class TestSignatureTypes:
    def test_status_values(self):
        assert SignatureStatus.VALID == "valid"
        assert SignatureStatus.MISSING == "missing"
        assert SignatureStatus.INVALID == "invalid"
        assert SignatureStatus.REVOKED == "revoked"
        assert SignatureStatus.UNKNOWN_KEY == "unknown_key"

    def test_result_with_key_id(self):
        r = SignatureResult(
            status=SignatureStatus.VALID,
            slug="test",
            key_id="ed25519:abc",
        )
        assert r.key_id == "ed25519:abc"
        assert r.error is None

    def test_result_with_error(self):
        r = SignatureResult(
            status=SignatureStatus.INVALID,
            slug="test",
            key_id="ed25519:abc",
            error="Verification failed",
        )
        assert r.error == "Verification failed"

    def test_algorithm_constant(self):
        assert SIGN_ALGORITHM == "ed25519"
