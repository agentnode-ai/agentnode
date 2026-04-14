"""OAuth2 PKCE flow — authorization URL generation and token exchange.

Supports: GitHub, Slack. No generic plugin system.
Config from env vars: OAUTH_{PROVIDER}_CLIENT_ID, OAUTH_{PROVIDER}_CLIENT_SECRET.
"""
from __future__ import annotations

import hashlib
import json as _json
import logging
import os
import secrets
import time
from base64 import urlsafe_b64encode
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import vault
from app.credentials.service import store_credential
from app.shared.exceptions import AppError

logger = logging.getLogger("agentnode.credentials.oauth")

_STATE_TTL_SECONDS = 300  # 5 minutes

# ---------------------------------------------------------------------------
# OAuth state store — Redis in production, in-memory fallback for dev/test
# ---------------------------------------------------------------------------

_fallback_states: dict[str, dict] = {}  # dev-only fallback


async def _get_redis():
    """Get the Redis client from the running FastAPI app.

    Returns None if Redis is unavailable (only acceptable in non-production).
    """
    try:
        from app.main import app
        redis = app.state.redis
        await redis.ping()
        return redis
    except Exception:
        return None


async def _store_state(state_token: str, payload: dict) -> None:
    """Store OAuth state. Uses Redis with TTL; falls back to dict in dev."""
    redis = await _get_redis()
    if redis is not None:
        key = f"oauth:state:{state_token}"
        await redis.set(key, _json.dumps(payload), ex=_STATE_TTL_SECONDS)
        return

    # Redis unavailable — check environment
    env = os.environ.get("ENVIRONMENT", "development")
    if env == "production":
        raise AppError(
            "OAUTH_STATE_STORE_UNAVAILABLE",
            "OAuth state store unavailable — Redis is required in production for OAuth flows",
            503,
        )

    logger.warning("Redis unavailable — using in-memory OAuth state store (dev-only)")
    _fallback_states[state_token] = payload


async def _pop_state(state_token: str) -> dict | None:
    """Retrieve and delete OAuth state. Returns None if not found / expired."""
    redis = await _get_redis()
    if redis is not None:
        key = f"oauth:state:{state_token}"
        raw = await redis.get(key)
        if raw is None:
            return None
        await redis.delete(key)
        return _json.loads(raw)

    # Redis unavailable — check environment
    env = os.environ.get("ENVIRONMENT", "development")
    if env == "production":
        raise AppError(
            "OAUTH_STATE_STORE_UNAVAILABLE",
            "OAuth state store unavailable — Redis is required in production for OAuth flows",
            503,
        )

    logger.warning("Redis unavailable — reading from in-memory OAuth state store (dev-only)")
    payload = _fallback_states.pop(state_token, None)
    if payload is None:
        return None
    # Manual TTL check for in-memory fallback
    if time.time() - payload.get("created_at", 0) > _STATE_TTL_SECONDS:
        return None
    return payload


# ---------------------------------------------------------------------------
# Provider configs — explicit, no registry
# ---------------------------------------------------------------------------

def _get_provider_config(provider: str) -> dict:
    """Get OAuth config for a known provider.

    Raises AppError if provider is not supported or env vars are missing.
    """
    provider = provider.lower()

    if provider == "github":
        client_id = os.environ.get("OAUTH_GITHUB_CLIENT_ID", "")
        client_secret = os.environ.get("OAUTH_GITHUB_CLIENT_SECRET", "")
        if not client_id or not client_secret:
            raise AppError(
                "OAUTH_NOT_CONFIGURED",
                "GitHub OAuth not configured (missing OAUTH_GITHUB_CLIENT_ID/SECRET)",
                501,
            )
        return {
            "provider": "github",
            "authorize_url": "https://github.com/login/oauth/authorize",
            "token_url": "https://github.com/login/oauth/access_token",
            "client_id": client_id,
            "client_secret": client_secret,
        }

    elif provider == "slack":
        client_id = os.environ.get("OAUTH_SLACK_CLIENT_ID", "")
        client_secret = os.environ.get("OAUTH_SLACK_CLIENT_SECRET", "")
        if not client_id or not client_secret:
            raise AppError(
                "OAUTH_NOT_CONFIGURED",
                "Slack OAuth not configured (missing OAUTH_SLACK_CLIENT_ID/SECRET)",
                501,
            )
        return {
            "provider": "slack",
            "authorize_url": "https://slack.com/oauth/v2/authorize",
            "token_url": "https://slack.com/api/oauth.v2.access",
            "client_id": client_id,
            "client_secret": client_secret,
        }

    else:
        raise AppError(
            "OAUTH_UNSUPPORTED_PROVIDER",
            f"OAuth not supported for provider '{provider}'. Supported: github, slack.",
            400,
        )


# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------

def _generate_pkce() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge (S256)."""
    code_verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def generate_auth_url(
    session: AsyncSession,
    *,
    connector_package_slug: str,
    scopes: list[str],
    user_id: str,
) -> dict:
    """Generate an OAuth2 authorization URL with PKCE.

    Returns: {"auth_url": str, "state": str}
    """
    # Resolve provider from the connector manifest
    from app.packages.models import Package, PackageVersion

    result = await session.execute(
        select(PackageVersion.manifest_raw)
        .join(Package, Package.id == PackageVersion.package_id)
        .where(Package.slug == connector_package_slug)
        .order_by(PackageVersion.published_at.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if not row or not isinstance(row, dict):
        raise AppError("OAUTH_NO_MANIFEST", f"No manifest found for '{connector_package_slug}'", 404)

    connector = row.get("connector", {})
    provider = connector.get("provider", "")
    if not provider:
        raise AppError("OAUTH_NO_PROVIDER", "Connector manifest has no provider field", 400)

    config = _get_provider_config(provider)

    # Generate PKCE
    code_verifier, code_challenge = _generate_pkce()

    # Generate state token
    state = secrets.token_urlsafe(32)

    # Store pending state (Redis with TTL, fallback to dict in dev)
    state_payload = {
        "user_id": user_id,
        "provider": provider,
        "connector_package_slug": connector_package_slug,
        "code_verifier": code_verifier,
        "scopes": scopes or connector.get("scopes", []),
        "created_at": time.time(),
    }
    await _store_state(state, state_payload)

    # Build authorization URL
    effective_scopes = scopes or connector.get("scopes", [])
    params = {
        "client_id": config["client_id"],
        "redirect_uri": _get_redirect_uri(),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }

    if provider == "github":
        params["scope"] = " ".join(effective_scopes)
    elif provider == "slack":
        params["scope"] = ",".join(effective_scopes)

    auth_url = f"{config['authorize_url']}?{urlencode(params)}"

    return {"auth_url": auth_url, "state": state}


async def exchange_code(
    session: AsyncSession,
    *,
    code: str,
    state: str,
) -> dict:
    """Exchange an authorization code for tokens, encrypt and store.

    Returns: {"provider": str, "stored": bool}
    """
    pending = await _pop_state(state)
    if not pending:
        raise AppError("OAUTH_INVALID_STATE", "Invalid or expired state token", 400)

    provider = pending["provider"]
    config = _get_provider_config(provider)

    # Exchange code for tokens
    token_data = {
        "client_id": config["client_id"],
        "client_secret": config["client_secret"],
        "code": code,
        "redirect_uri": _get_redirect_uri(),
        "code_verifier": pending["code_verifier"],
    }

    if provider == "github":
        token_data["grant_type"] = "authorization_code"
        headers = {"Accept": "application/json"}
    elif provider == "slack":
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
    else:
        headers = {"Accept": "application/json"}

    async with httpx.AsyncClient() as client:
        resp = await client.post(config["token_url"], data=token_data, headers=headers, timeout=15.0)

    if resp.status_code != 200:
        raise AppError(
            "OAUTH_TOKEN_EXCHANGE_FAILED",
            f"Token exchange failed: HTTP {resp.status_code}",
            502,
        )

    token_response = resp.json()

    # Extract access token
    access_token = token_response.get("access_token")
    if not access_token:
        # Slack nests the token
        authed_user = token_response.get("authed_user", {})
        access_token = authed_user.get("access_token")

    if not access_token:
        raise AppError("OAUTH_NO_TOKEN", "No access_token in token response", 502)

    # Build secret_data
    secret_data = {"access_token": access_token}
    refresh_token = token_response.get("refresh_token")
    if refresh_token:
        secret_data["refresh_token"] = refresh_token

    # Resolve allowed domains from manifest
    from app.credentials.router import _resolve_connector_domains
    allowed_domains = await _resolve_connector_domains(session, pending["connector_package_slug"])

    # Store encrypted credential
    from uuid import UUID as _UUID
    await store_credential(
        session,
        user_id=_UUID(pending["user_id"]),
        connector_provider=provider,
        connector_package_slug=pending["connector_package_slug"],
        auth_type="oauth2",
        secret_data=secret_data,
        scopes=pending["scopes"],
        allowed_domains=allowed_domains,
    )

    return {"provider": provider, "stored": True}


def _get_redirect_uri() -> str:
    """Get the OAuth redirect URI from environment."""
    return os.environ.get(
        "OAUTH_REDIRECT_URI",
        "http://localhost:8000/v1/credentials/oauth/callback",
    )
