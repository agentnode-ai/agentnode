"""Tests for publisher signing key management — Phase 16.2."""
import os
import stat
import sys
import warnings

import pytest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
)

from agentnode_sdk.signing_key import (
    compute_key_id,
    generate_ed25519_keypair,
    get_or_create_signing_key,
    get_signing_key_path,
    load_signing_key,
    save_signing_key,
    sign_payload,
    _check_key_permissions,
)
from agentnode_sdk.signature import verify_signature


# ---------------------------------------------------------------------------
# generate_ed25519_keypair
# ---------------------------------------------------------------------------

class TestGenerateKeypair:
    def test_returns_private_key_and_public_bytes(self):
        private_key, pub_bytes = generate_ed25519_keypair()
        assert isinstance(private_key, Ed25519PrivateKey)
        assert isinstance(pub_bytes, bytes)
        assert len(pub_bytes) == 32

    def test_different_keys_each_call(self):
        _, pub1 = generate_ed25519_keypair()
        _, pub2 = generate_ed25519_keypair()
        assert pub1 != pub2

    def test_public_key_matches_private(self):
        private_key, pub_bytes = generate_ed25519_keypair()
        derived = private_key.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw,
        )
        assert derived == pub_bytes


# ---------------------------------------------------------------------------
# sign_payload + verify round-trip
# ---------------------------------------------------------------------------

class TestSignPayload:
    def test_sign_verify_roundtrip(self):
        private_key, pub_bytes = generate_ed25519_keypair()
        payload = b"test payload for signing"
        sig = sign_payload(payload, private_key)
        assert isinstance(sig, bytes)
        assert len(sig) == 64
        assert verify_signature(payload, sig, pub_bytes) is True

    def test_signature_rejects_tampered_payload(self):
        private_key, pub_bytes = generate_ed25519_keypair()
        sig = sign_payload(b"original", private_key)
        assert verify_signature(b"tampered", sig, pub_bytes) is False

    def test_signature_rejects_wrong_key(self):
        priv1, _ = generate_ed25519_keypair()
        _, pub2 = generate_ed25519_keypair()
        sig = sign_payload(b"payload", priv1)
        assert verify_signature(b"payload", sig, pub2) is False


# ---------------------------------------------------------------------------
# compute_key_id
# ---------------------------------------------------------------------------

class TestComputeKeyId:
    def test_deterministic(self):
        _, pub_bytes = generate_ed25519_keypair()
        kid1 = compute_key_id(pub_bytes)
        kid2 = compute_key_id(pub_bytes)
        assert kid1 == kid2

    def test_format(self):
        _, pub_bytes = generate_ed25519_keypair()
        kid = compute_key_id(pub_bytes)
        assert kid.startswith("ed25519:")
        hex_part = kid.split(":")[1]
        assert len(hex_part) == 16
        int(hex_part, 16)  # must be valid hex

    def test_different_keys_different_ids(self):
        _, pub1 = generate_ed25519_keypair()
        _, pub2 = generate_ed25519_keypair()
        assert compute_key_id(pub1) != compute_key_id(pub2)


# ---------------------------------------------------------------------------
# save / load round-trip
# ---------------------------------------------------------------------------

class TestSaveLoadKey:
    def test_save_load_roundtrip(self, tmp_path):
        private_key, pub_bytes = generate_ed25519_keypair()
        key_path = tmp_path / "signing_key"
        save_signing_key(private_key, path=key_path)

        loaded = load_signing_key(path=key_path)
        loaded_pub = loaded.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw,
        )
        assert loaded_pub == pub_bytes

    def test_save_creates_pem_file(self, tmp_path):
        private_key, _ = generate_ed25519_keypair()
        key_path = tmp_path / "signing_key"
        save_signing_key(private_key, path=key_path)
        content = key_path.read_bytes()
        assert b"-----BEGIN PRIVATE KEY-----" in content

    def test_save_creates_parent_dirs(self, tmp_path):
        private_key, _ = generate_ed25519_keypair()
        key_path = tmp_path / "nested" / "dir" / "signing_key"
        save_signing_key(private_key, path=key_path)
        assert key_path.is_file()

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX only")
    def test_save_sets_0600_permissions(self, tmp_path):
        private_key, _ = generate_ed25519_keypair()
        key_path = tmp_path / "signing_key"
        save_signing_key(private_key, path=key_path)
        mode = key_path.stat().st_mode & 0o777
        assert mode == 0o600

    def test_load_nonexistent_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Signing key not found"):
            load_signing_key(path=tmp_path / "nonexistent")

    def test_load_invalid_pem_raises(self, tmp_path):
        bad_path = tmp_path / "signing_key"
        bad_path.write_text("not a valid PEM key")
        with pytest.raises(ValueError, match="Invalid signing key"):
            load_signing_key(path=bad_path)

    def test_signed_with_loaded_key_verifies(self, tmp_path):
        """Full round-trip: generate → save → load → sign → verify."""
        private_key, pub_bytes = generate_ed25519_keypair()
        key_path = tmp_path / "signing_key"
        save_signing_key(private_key, path=key_path)

        loaded = load_signing_key(path=key_path)
        payload = b"integrity payload"
        sig = sign_payload(payload, loaded)
        assert verify_signature(payload, sig, pub_bytes) is True


# ---------------------------------------------------------------------------
# get_or_create_signing_key
# ---------------------------------------------------------------------------

class TestGetOrCreateSigningKey:
    def test_creates_new_key(self, tmp_path):
        key_path = tmp_path / "signing_key"
        assert not key_path.exists()
        private_key = get_or_create_signing_key(path=key_path)
        assert isinstance(private_key, Ed25519PrivateKey)
        assert key_path.is_file()

    def test_reuses_existing_key(self, tmp_path):
        key_path = tmp_path / "signing_key"
        key1 = get_or_create_signing_key(path=key_path)
        pub1 = key1.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        key2 = get_or_create_signing_key(path=key_path)
        pub2 = key2.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        assert pub1 == pub2

    def test_key_can_sign_and_verify(self, tmp_path):
        key_path = tmp_path / "signing_key"
        private_key = get_or_create_signing_key(path=key_path)
        pub_bytes = private_key.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw,
        )
        sig = sign_payload(b"test", private_key)
        assert verify_signature(b"test", sig, pub_bytes) is True


# ---------------------------------------------------------------------------
# get_signing_key_path
# ---------------------------------------------------------------------------

class TestGetSigningKeyPath:
    def test_returns_path_under_config_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path))
        path = get_signing_key_path()
        assert path == tmp_path / "signing_key"
        assert path.parent == tmp_path

    def test_default_path_ends_with_signing_key(self):
        path = get_signing_key_path()
        assert path.name == "signing_key"


# ---------------------------------------------------------------------------
# _check_key_permissions
# ---------------------------------------------------------------------------

class TestCheckKeyPermissions:
    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX only")
    def test_warns_on_group_readable(self, tmp_path):
        key_path = tmp_path / "signing_key"
        key_path.write_text("fake key")
        os.chmod(key_path, 0o640)
        with pytest.warns(match="too-open permissions"):
            _check_key_permissions(key_path)

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX only")
    def test_warns_on_world_readable(self, tmp_path):
        key_path = tmp_path / "signing_key"
        key_path.write_text("fake key")
        os.chmod(key_path, 0o644)
        with pytest.warns(match="too-open permissions"):
            _check_key_permissions(key_path)

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX only")
    def test_no_warning_on_0600(self, tmp_path):
        key_path = tmp_path / "signing_key"
        key_path.write_text("fake key")
        os.chmod(key_path, 0o600)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _check_key_permissions(key_path)
            assert len(w) == 0

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
    def test_windows_does_not_fail(self, tmp_path):
        key_path = tmp_path / "signing_key"
        key_path.write_text("fake key")
        _check_key_permissions(key_path)
