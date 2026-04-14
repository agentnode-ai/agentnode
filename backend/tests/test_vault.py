"""Tests for credential vault encryption/decryption."""
import os
import pytest

from cryptography.fernet import Fernet


@pytest.fixture(autouse=True)
def _set_encryption_key(monkeypatch):
    """Set a valid Fernet key for all tests."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", key)


class TestVaultEncryption:
    def test_encrypt_decrypt_roundtrip(self):
        from app.credentials.vault import encrypt, decrypt

        data = {"api_key": "sk-test-secret-12345", "extra": "value"}
        encrypted = encrypt(data)

        # Encrypted string should NOT contain the plaintext
        assert "sk-test-secret-12345" not in encrypted

        # Decrypt should return the original data
        result = decrypt(encrypted)
        assert result == data

    def test_encrypt_produces_different_output_each_time(self):
        from app.credentials.vault import encrypt

        data = {"api_key": "same-key"}
        a = encrypt(data)
        b = encrypt(data)
        # Fernet includes a timestamp + random IV, so outputs differ
        assert a != b

    def test_decrypt_with_wrong_key_fails(self, monkeypatch):
        from app.credentials.vault import encrypt, decrypt

        data = {"token": "secret"}
        encrypted = encrypt(data)

        # Change the key
        new_key = Fernet.generate_key().decode()
        monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", new_key)

        with pytest.raises(RuntimeError, match="Failed to decrypt"):
            decrypt(encrypted)

    def test_decrypt_tampered_data_fails(self):
        from app.credentials.vault import encrypt, decrypt

        encrypted = encrypt({"key": "value"})
        # Tamper with the ciphertext
        tampered = encrypted[:-5] + "XXXXX"
        with pytest.raises(RuntimeError, match="Failed to decrypt"):
            decrypt(tampered)

    def test_missing_encryption_key_raises(self, monkeypatch):
        monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEY", raising=False)

        from app.credentials.vault import encrypt
        with pytest.raises(RuntimeError, match="CREDENTIAL_ENCRYPTION_KEY is not set"):
            encrypt({"key": "value"})

    def test_empty_dict_roundtrip(self):
        from app.credentials.vault import encrypt, decrypt

        data = {}
        result = decrypt(encrypt(data))
        assert result == {}

    def test_unicode_data_roundtrip(self):
        from app.credentials.vault import encrypt, decrypt

        data = {"name": "Schlüssel", "emoji": "🔐", "jp": "秘密"}
        result = decrypt(encrypt(data))
        assert result == data

    def test_nested_dict_roundtrip(self):
        from app.credentials.vault import encrypt, decrypt

        data = {
            "oauth2": {
                "access_token": "at-123",
                "refresh_token": "rt-456",
                "expires_in": 3600,
            }
        }
        result = decrypt(encrypt(data))
        assert result == data
