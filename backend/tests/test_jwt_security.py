"""Negative tests for JWT authentication security (Audit P0.1).

Verifies that the JWT authentication layer correctly rejects:
  1. Expired tokens
  2. Tokens signed with the wrong secret
  3. Tokens missing the token_type claim
  4. Refresh tokens used as access tokens (wrong token_type)
  5. Tampered payloads (modified after signing)
  6. Empty / malformed / garbage bearer tokens
  7. Tokens using the "none" algorithm
"""

import base64
import json
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.config import settings

# A protected endpoint that requires get_current_user
PROTECTED_ENDPOINT = "/v1/auth/me"

# Reusable test user for registration
JWT_TEST_USER = {
    "email": "jwttest@agentnode.dev",
    "username": "jwttestuser",
    "password": "JwtTestPass123!",
}


def _make_auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _make_token(payload: dict, secret: str = settings.JWT_SECRET, algorithm: str = settings.JWT_ALGORITHM) -> str:
    """Create a JWT with the given payload, secret, and algorithm."""
    return jwt.encode(payload, secret, algorithm=algorithm)


# ---------------------------------------------------------------------------
# 1. Expired token
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_expired_token_rejected(client):
    """A token whose exp is in the past must be rejected with 401."""
    payload = {
        "sub": "fake-user-id",
        "token_type": "access",
        "exp": datetime.now(timezone.utc) - timedelta(seconds=30),
    }
    token = _make_token(payload)
    resp = await client.get(PROTECTED_ENDPOINT, headers=_make_auth_header(token))
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 2. Wrong signing key
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_wrong_signing_key_rejected(client):
    """A token signed with a different secret must be rejected with 401."""
    payload = {
        "sub": "fake-user-id",
        "token_type": "access",
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    wrong_secret = "this-is-not-the-real-secret-key-at-all"
    assert wrong_secret != settings.JWT_SECRET, "Test requires a different secret"
    token = _make_token(payload, secret=wrong_secret)
    resp = await client.get(PROTECTED_ENDPOINT, headers=_make_auth_header(token))
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 3. Missing token_type claim
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_token_type_rejected(client):
    """A token without a token_type claim must be rejected with 401."""
    payload = {
        "sub": "fake-user-id",
        # no token_type
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    token = _make_token(payload)
    resp = await client.get(PROTECTED_ENDPOINT, headers=_make_auth_header(token))
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 4. Wrong token_type (refresh used as access)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_refresh_token_rejected_as_access(client):
    """A refresh token must not be accepted where an access token is required."""
    # Register + login to get a real refresh token
    await client.post("/v1/auth/register", json=JWT_TEST_USER)
    login_resp = await client.post("/v1/auth/login", json={
        "email": JWT_TEST_USER["email"],
        "password": JWT_TEST_USER["password"],
    })
    refresh_token = login_resp.json()["refresh_token"]

    resp = await client.get(PROTECTED_ENDPOINT, headers=_make_auth_header(refresh_token))
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_wrong_token_type_synthetic(client):
    """A hand-crafted token with token_type='refresh' must be rejected on access endpoints."""
    payload = {
        "sub": "fake-user-id",
        "token_type": "refresh",
        "jti": "fake-jti",
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    token = _make_token(payload)
    resp = await client.get(PROTECTED_ENDPOINT, headers=_make_auth_header(token))
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_purpose_token_rejected_as_access(client):
    """A token with token_type='email_verify' must be rejected as an access token."""
    payload = {
        "sub": "fake-user-id",
        "token_type": "email_verify",
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    token = _make_token(payload)
    resp = await client.get(PROTECTED_ENDPOINT, headers=_make_auth_header(token))
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 5. Tampered payload
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tampered_payload_rejected(client):
    """Modifying the payload after signing must invalidate the token."""
    # Create a legitimate token
    payload = {
        "sub": "original-user-id",
        "token_type": "access",
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    token = _make_token(payload)

    # Split into header.payload.signature
    parts = token.split(".")
    assert len(parts) == 3

    # Decode payload, tamper with it, re-encode
    # Add padding to base64url string
    padded = parts[1] + "=" * (4 - len(parts[1]) % 4)
    decoded_payload = json.loads(base64.urlsafe_b64decode(padded))
    decoded_payload["sub"] = "tampered-admin-id"
    tampered_bytes = json.dumps(decoded_payload, separators=(",", ":")).encode()
    tampered_b64 = base64.urlsafe_b64encode(tampered_bytes).rstrip(b"=").decode()

    # Re-assemble with original header + signature but tampered payload
    tampered_token = f"{parts[0]}.{tampered_b64}.{parts[2]}"

    resp = await client.get(PROTECTED_ENDPOINT, headers=_make_auth_header(tampered_token))
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 6. Empty / malformed / garbage tokens
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("bad_token", [
    "",                                  # empty string
    "not-a-jwt",                         # no dots at all
    "a.b",                               # only two parts
    "a.b.c.d",                           # four parts
    "eyJhbGciOiJIUzI1NiJ9..sig",        # empty payload
    "!!!.@@@.###",                        # special characters
    " ",                                  # whitespace
    "Bearer nested",                      # someone accidentally double-wrapping
    "null",                               # literal null
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.",  # missing signature
])
async def test_malformed_token_rejected(client, bad_token):
    """Garbage / malformed bearer values must be rejected with 401."""
    resp = await client.get(PROTECTED_ENDPOINT, headers=_make_auth_header(bad_token))
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_no_authorization_header_rejected(client):
    """Requests without any auth header to a protected endpoint must get 401."""
    resp = await client.get(PROTECTED_ENDPOINT)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_bearer_prefix_only_rejected(client):
    """'Bearer ' with no actual token value must be rejected."""
    resp = await client.get(PROTECTED_ENDPOINT, headers={"Authorization": "Bearer "})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_wrong_auth_scheme_rejected(client):
    """A non-Bearer auth scheme must be rejected."""
    payload = {
        "sub": "fake-user-id",
        "token_type": "access",
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    token = _make_token(payload)
    resp = await client.get(PROTECTED_ENDPOINT, headers={"Authorization": f"Basic {token}"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 7. Algorithm confusion — "none" algorithm
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_none_algorithm_rejected(client):
    """A token crafted with alg='none' (unsigned) must be rejected."""
    # Manually construct a JWT with alg: none
    header = {"alg": "none", "typ": "JWT"}
    payload = {
        "sub": "fake-user-id",
        "token_type": "access",
        "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()),
    }
    header_b64 = base64.urlsafe_b64encode(
        json.dumps(header, separators=(",", ":")).encode()
    ).rstrip(b"=").decode()
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).rstrip(b"=").decode()

    # alg=none tokens have an empty signature segment
    none_token = f"{header_b64}.{payload_b64}."

    resp = await client.get(PROTECTED_ENDPOINT, headers=_make_auth_header(none_token))
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_hs384_algorithm_rejected(client):
    """A validly-signed token using HS384 (instead of the configured HS256) must be rejected."""
    payload = {
        "sub": "fake-user-id",
        "token_type": "access",
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    token = _make_token(payload, algorithm="HS384")
    resp = await client.get(PROTECTED_ENDPOINT, headers=_make_auth_header(token))
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_hs512_algorithm_rejected(client):
    """A validly-signed token using HS512 (instead of the configured HS256) must be rejected."""
    payload = {
        "sub": "fake-user-id",
        "token_type": "access",
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    token = _make_token(payload, algorithm="HS512")
    resp = await client.get(PROTECTED_ENDPOINT, headers=_make_auth_header(token))
    assert resp.status_code == 401
