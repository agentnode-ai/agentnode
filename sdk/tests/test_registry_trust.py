"""Tests for registry response authenticity (TG-4)."""
from __future__ import annotations

import base64
from types import MappingProxyType
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agentnode_sdk.registry_trust import (
    REGISTRY_KEYS,
    RegistryKey,
    RegistrySignatureResult,
    RegistrySignatureStatus,
    SIGNATURE_HEADER,
    _ED25519_KEY_LENGTH,
    _ED25519_SIG_LENGTH,
    _MAX_HEADER_LENGTH,
    enforcement_active,
    is_trust_critical,
    parse_signature_header,
    verify_registry_response,
)


# --- Helpers ---

def _make_test_key() -> tuple[RegistryKey, Ed25519PrivateKey]:
    """Generate a test registry key pair."""
    private = Ed25519PrivateKey.generate()
    pub_bytes = private.public_key().public_bytes_raw()
    pub_b64 = base64.b64encode(pub_bytes).decode()
    key = RegistryKey(
        key_id="test-key-1",
        algorithm="ed25519",
        public_key=pub_b64,
        not_after="2099-12-31",
    )
    return key, private


def _sign(private: Ed25519PrivateKey, body: bytes) -> str:
    """Create a valid signature header."""
    sig_bytes = private.sign(body)
    sig_b64 = base64.b64encode(sig_bytes).decode()
    return f"ed25519:test-key-1:{sig_b64}"


def _patch_keys(keys: dict):
    """Context manager to temporarily set REGISTRY_KEYS."""
    return patch(
        "agentnode_sdk.registry_trust.REGISTRY_KEYS",
        MappingProxyType(keys),
    )


# =========================================================================
# Header parsing
# =========================================================================

class TestParseSignatureHeader:

    def test_valid_header(self):
        sig = base64.b64encode(b"\x00" * 64).decode()
        header = f"ed25519:test-key:{sig}"
        alg, kid, sig_bytes = parse_signature_header(header)
        assert alg == "ed25519"
        assert kid == "test-key"
        assert len(sig_bytes) == 64

    def test_invalid_format_no_colon(self):
        with pytest.raises(ValueError, match="algorithm:key_id:base64sig"):
            parse_signature_header("just-a-string")

    def test_missing_parts_two_colons(self):
        with pytest.raises(ValueError, match="algorithm:key_id:base64sig"):
            parse_signature_header("ed25519:key-only")

    def test_oversized_header(self):
        with pytest.raises(ValueError, match="exceeds"):
            parse_signature_header("a" * (_MAX_HEADER_LENGTH + 1))

    def test_wrong_signature_length(self):
        sig = base64.b64encode(b"\x00" * 32).decode()
        with pytest.raises(ValueError, match="64 bytes"):
            parse_signature_header(f"ed25519:test-key:{sig}")

    def test_invalid_base64(self):
        with pytest.raises(ValueError, match="Invalid base64"):
            parse_signature_header("ed25519:test-key:!!!not-base64!!!")

    def test_invalid_key_id_unicode(self):
        sig = base64.b64encode(b"\x00" * 64).decode()
        with pytest.raises(ValueError, match="Invalid key_id"):
            parse_signature_header(f"ed25519:ünïcödé:{sig}")

    def test_key_id_too_long(self):
        sig = base64.b64encode(b"\x00" * 64).decode()
        long_id = "a" * 65
        with pytest.raises(ValueError, match="Invalid key_id"):
            parse_signature_header(f"ed25519:{long_id}:{sig}")

    def test_unsupported_algorithm(self):
        sig = base64.b64encode(b"\x00" * 64).decode()
        with pytest.raises(ValueError, match="Unsupported algorithm"):
            parse_signature_header(f"rsa:test-key:{sig}")


# =========================================================================
# Verification
# =========================================================================

class TestVerifyRegistryResponse:

    def test_valid_signature(self):
        key, private = _make_test_key()
        body = b'{"slug": "test-pack", "version": "1.0.0"}'
        header = _sign(private, body)
        with _patch_keys({"test-key-1": key}):
            result = verify_registry_response(body, header)
        assert result.status == RegistrySignatureStatus.VALID
        assert result.key_id == "test-key-1"

    def test_invalid_signature_wrong_body(self):
        key, private = _make_test_key()
        body = b'{"slug": "test-pack"}'
        header = _sign(private, b"different body")
        with _patch_keys({"test-key-1": key}):
            result = verify_registry_response(body, header)
        assert result.status == RegistrySignatureStatus.INVALID
        assert "verification failed" in result.error

    def test_unknown_key_id(self):
        key, private = _make_test_key()
        body = b'{"data": true}'
        header = _sign(private, body)
        other_key = RegistryKey(
            key_id="other-key",
            algorithm="ed25519",
            public_key=key.public_key,
        )
        with _patch_keys({"other-key": other_key}):
            result = verify_registry_response(body, header)
        assert result.status == RegistrySignatureStatus.UNKNOWN_KEY
        assert result.key_id == "test-key-1"

    def test_expired_key(self):
        key_expired = RegistryKey(
            key_id="test-key-1",
            algorithm="ed25519",
            public_key=base64.b64encode(b"\x00" * 32).decode(),
            not_after="2020-01-01",
        )
        _, private = _make_test_key()
        body = b'{"data": true}'
        header = _sign(private, body)
        with _patch_keys({"test-key-1": key_expired}):
            result = verify_registry_response(body, header)
        assert result.status == RegistrySignatureStatus.EXPIRED_KEY
        assert result.key_id == "test-key-1"

    def test_algorithm_mismatch(self):
        key, private = _make_test_key()
        body = b'{"data": true}'
        sig = base64.b64encode(private.sign(body)).decode()
        header = f"ed25519:test-key-1:{sig}"
        bad_key = RegistryKey(
            key_id="test-key-1",
            algorithm="ed448",
            public_key=key.public_key,
        )
        with _patch_keys({"test-key-1": bad_key}):
            result = verify_registry_response(body, header)
        assert result.status == RegistrySignatureStatus.INVALID
        assert "Algorithm mismatch" in result.error

    def test_corrupt_pinned_key_base64(self):
        bad_key = RegistryKey(
            key_id="test-key-1",
            algorithm="ed25519",
            public_key="!!!not-base64!!!",
        )
        _, private = _make_test_key()
        body = b'{"data": true}'
        header = _sign(private, body)
        with _patch_keys({"test-key-1": bad_key}):
            result = verify_registry_response(body, header)
        assert result.status == RegistrySignatureStatus.INVALID
        assert "pinned public key" in result.error

    def test_wrong_pinned_key_length(self):
        bad_key = RegistryKey(
            key_id="test-key-1",
            algorithm="ed25519",
            public_key=base64.b64encode(b"\x00" * 16).decode(),
        )
        _, private = _make_test_key()
        body = b'{"data": true}'
        header = _sign(private, body)
        with _patch_keys({"test-key-1": bad_key}):
            result = verify_registry_response(body, header)
        assert result.status == RegistrySignatureStatus.INVALID
        assert f"{_ED25519_KEY_LENGTH} bytes" in result.error

    def test_malformed_header(self):
        key, _ = _make_test_key()
        with _patch_keys({"test-key-1": key}):
            result = verify_registry_response(b"body", "bad-header")
        assert result.status == RegistrySignatureStatus.INVALID


# =========================================================================
# Activation semantics
# =========================================================================

class TestActivationSemantics:

    def test_no_keys_no_header_bootstrap(self):
        with _patch_keys({}):
            result = verify_registry_response(b"body", None)
        assert result.status == RegistrySignatureStatus.BOOTSTRAP
        assert result.error is None

    def test_no_keys_header_present_bootstrap(self):
        """Even with a signature header, empty REGISTRY_KEYS → BOOTSTRAP."""
        sig = base64.b64encode(b"\x00" * 64).decode()
        header = f"ed25519:some-key:{sig}"
        with _patch_keys({}):
            result = verify_registry_response(b"body", header)
        assert result.status == RegistrySignatureStatus.BOOTSTRAP

    def test_keys_populated_no_header_missing(self):
        key, _ = _make_test_key()
        with _patch_keys({"test-key-1": key}):
            result = verify_registry_response(b"body", None)
        assert result.status == RegistrySignatureStatus.MISSING
        assert "no signature" in result.error

    def test_enforcement_active_empty(self):
        assert enforcement_active() is False

    def test_enforcement_active_populated(self):
        key, _ = _make_test_key()
        with _patch_keys({"test-key-1": key}):
            assert enforcement_active() is True


# =========================================================================
# Immutability
# =========================================================================

class TestImmutability:

    def test_registry_keys_immutable(self):
        with pytest.raises(TypeError):
            REGISTRY_KEYS["new-key"] = "anything"


# =========================================================================
# Endpoint matching
# =========================================================================

class TestIsTrustCritical:

    def test_install_info(self):
        assert is_trust_critical("/v1/packages/my-pack/install-info") is True

    def test_package_detail(self):
        assert is_trust_critical("/v1/packages/my-pack") is True

    def test_key_status(self):
        assert is_trust_critical("/v1/publishers/acme/keys/ed25519-k1") is True

    def test_not_search(self):
        assert is_trust_critical("/v1/search") is False

    def test_not_package_subpath(self):
        assert is_trust_critical("/v1/packages/my-pack/stats") is False

    def test_not_download(self):
        assert is_trust_critical("/v1/packages/my-pack/download") is False

    def test_without_v1_prefix(self):
        assert is_trust_critical("/packages/my-pack") is True

    def test_install_info_without_v1(self):
        assert is_trust_critical("/packages/my-pack/install-info") is True

    def test_key_status_without_v1(self):
        assert is_trust_critical("/publishers/acme/keys/k1") is True

    def test_trailing_slash_stripped(self):
        assert is_trust_critical("/v1/packages/my-pack/") is True

    def test_not_install_endpoint(self):
        assert is_trust_critical("/v1/packages/my-pack/install") is False

    def test_not_resolve(self):
        assert is_trust_critical("/v1/resolve") is False
