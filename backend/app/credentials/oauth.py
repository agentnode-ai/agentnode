"""OAuth2 PKCE flow — authorization URL generation and token exchange.

Supports: GitHub, Slack. No generic plugin system.
Config from env vars: OAUTH_{PROVIDER}_CLIENT_ID, OAUTH_{PROVIDER}_CLIENT_SECRET.
"""
from __future__ import annotations

import hashlib
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

# In-memory state store (production: use Redis with TTL)
# {state_token: {user_id, provider, slug, code_verifier, scopes, created_at}}
_pending_states: dict[str, dict] = {}
_STATE_TTL_SECONDS = 300  # 5 minutes


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


def _resolve_provider_from_manifest(session, connector_package_slug: str) -> str:
    """Extract the provider name from a connector manifest (sync helper)."""
    # This is called from async context, so we receive the already-fetched value
    raise NotImplementedError("Use async version")


# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------

def _generate_pkce() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge (S256)."""
    code_verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def _cleanup_expired_states() -> None:
    """Remove expired state tokens."""
    now = time.time()
    expired = [k for k, v in _pending_states.items() if now - v["created_at"] > _STATE_TTL_SECONDS]
    for k in expired:
        del _pending_states[k]


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

    # Store pending state
    _cleanup_expired_states()
    _pending_states[state] = {
        "user_id": user_id,
        "provider": provider,
        "connector_package_slug": connector_package_slug,
        "code_verifier": code_verifier,
        "scopes": scopes or connector.get("scopes", []),
        "created_at": time.time(),
    }

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
    _cleanup_expired_states()

    pending = _pending_states.pop(state, None)
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
