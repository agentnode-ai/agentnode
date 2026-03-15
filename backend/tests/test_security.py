"""Unit tests for security utilities."""
from app.auth.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_api_key,
    hash_api_key,
    hash_password,
    verify_password,
)


def test_password_hashing():
    hashed = hash_password("mypassword")
    assert verify_password("mypassword", hashed)
    assert not verify_password("wrongpassword", hashed)


def test_api_key_generation():
    full_key, prefix, key_hash = generate_api_key()
    assert full_key.startswith("ank_")
    assert prefix == full_key[:8]
    assert hash_api_key(full_key) == key_hash


def test_jwt_access_token():
    token = create_access_token("user-123")
    payload = decode_token(token)
    assert payload["sub"] == "user-123"
    assert payload["token_type"] == "access"


def test_jwt_refresh_token():
    token = create_refresh_token("user-123")
    payload = decode_token(token)
    assert payload["sub"] == "user-123"
    assert payload["token_type"] == "refresh"
