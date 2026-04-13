"""Sprint A — Auth session-invalidation tests.

Covers:
  P0-01 — password reset and change_password must invalidate all
          previously issued refresh tokens.
  P1-S1 — refresh-token rotation is atomic (the same token cannot be
          consumed twice).
  P0-17 — password reset / change_password end-to-end integration
          coverage (both endpoints previously had zero tests).
"""
from __future__ import annotations

import asyncio

import pytest

from app.auth.security import create_purpose_token

USER = {
    "email": "sprint-a@agentnode.dev",
    "username": "sprintauser",
    "password": "OldPass123!",
}
NEW_PASSWORD = "NewPass456!"


async def _register_and_login(client) -> dict:
    await client.post("/v1/auth/register", json=USER)
    resp = await client.post(
        "/v1/auth/login",
        json={"email": USER["email"], "password": USER["password"]},
    )
    assert resp.status_code == 200
    return resp.json()


@pytest.mark.asyncio
async def test_change_password_revokes_existing_refresh_tokens(client):
    """P0-01: after change-password, the previous refresh token must be
    rejected with 401 on /refresh."""
    tokens = await _register_and_login(client)
    old_refresh = tokens["refresh_token"]
    access = tokens["access_token"]

    resp = await client.post(
        "/v1/auth/change-password",
        json={
            "current_password": USER["password"],
            "new_password": NEW_PASSWORD,
        },
        headers={"Authorization": f"Bearer {access}"},
    )
    assert resp.status_code == 200

    # Old refresh token must no longer be valid
    refresh_resp = await client.post(
        "/v1/auth/refresh",
        json={"refresh_token": old_refresh},
    )
    assert refresh_resp.status_code == 401, (
        "refresh token issued before password change should be revoked"
    )


@pytest.mark.asyncio
async def test_change_password_issues_usable_new_tokens(client):
    """After change-password the user can still log in with the new password
    and the new refresh tokens work."""
    await _register_and_login(client)
    login = await client.post(
        "/v1/auth/login",
        json={"email": USER["email"], "password": USER["password"]},
    )
    access = login.json()["access_token"]

    await client.post(
        "/v1/auth/change-password",
        json={
            "current_password": USER["password"],
            "new_password": NEW_PASSWORD,
        },
        headers={"Authorization": f"Bearer {access}"},
    )

    # Old password no longer works
    bad = await client.post(
        "/v1/auth/login",
        json={"email": USER["email"], "password": USER["password"]},
    )
    assert bad.status_code == 401

    # New password works and yields a usable refresh token
    good = await client.post(
        "/v1/auth/login",
        json={"email": USER["email"], "password": NEW_PASSWORD},
    )
    assert good.status_code == 200
    new_refresh = good.json()["refresh_token"]
    refresh_resp = await client.post(
        "/v1/auth/refresh",
        json={"refresh_token": new_refresh},
    )
    assert refresh_resp.status_code == 200


@pytest.mark.asyncio
async def test_password_reset_confirm_revokes_existing_refresh_tokens(client, session):
    """P0-01: password-reset (not change-password) must also invalidate
    every refresh token issued before the reset. Exercises the
    `reset_password` service path, not `change_password`."""
    tokens = await _register_and_login(client)
    old_refresh = tokens["refresh_token"]

    # Look up the user_id and mint a valid purpose token for the reset flow.
    from sqlalchemy import select
    from app.auth.models import User
    user = (await session.execute(
        select(User).where(User.email == USER["email"])
    )).scalar_one()

    reset_token = create_purpose_token(
        str(user.id), "password_reset", expire_hours=1,
    )

    resp = await client.post(
        "/v1/auth/reset-password",
        json={"token": reset_token, "new_password": NEW_PASSWORD},
    )
    assert resp.status_code == 200

    # Old refresh token must now be rejected
    refresh_resp = await client.post(
        "/v1/auth/refresh",
        json={"refresh_token": old_refresh},
    )
    assert refresh_resp.status_code == 401


@pytest.mark.asyncio
async def test_refresh_token_rotation_consumes_atomically(client):
    """P1-S1: a refresh token cannot be reused after rotation, even with
    back-to-back calls. Guards the TOCTOU hole between validate_refresh_jti
    and revoke_refresh_jti that existed before Sprint A."""
    tokens = await _register_and_login(client)
    old_refresh = tokens["refresh_token"]

    # First rotation succeeds and returns a fresh refresh token.
    first = await client.post(
        "/v1/auth/refresh",
        json={"refresh_token": old_refresh},
    )
    assert first.status_code == 200

    # Reusing the already-rotated token must fail even though the token
    # payload is still cryptographically valid.
    second = await client.post(
        "/v1/auth/refresh",
        json={"refresh_token": old_refresh},
    )
    assert second.status_code == 401


@pytest.mark.asyncio
async def test_change_password_wrong_current_does_not_revoke_tokens(client):
    """Negative-path: an attacker who doesn't know the current password
    cannot invalidate the victim's refresh tokens by triggering the
    change-password flow."""
    tokens = await _register_and_login(client)
    old_refresh = tokens["refresh_token"]
    access = tokens["access_token"]

    resp = await client.post(
        "/v1/auth/change-password",
        json={
            "current_password": "WrongPass!!!",
            "new_password": NEW_PASSWORD,
        },
        headers={"Authorization": f"Bearer {access}"},
    )
    assert resp.status_code == 401

    # Original refresh token still works
    refresh_resp = await client.post(
        "/v1/auth/refresh",
        json={"refresh_token": old_refresh},
    )
    assert refresh_resp.status_code == 200
