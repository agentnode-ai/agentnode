"""Credential CRUD endpoints + OAuth callback skeleton.

Policy: trust >= verified required for credential operations.
Secrets never appear in responses.
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.models import User
from app.credentials.schemas import (
    CredentialListResponse,
    CredentialStoreResponse,
    CredentialSummary,
    CredentialTestResponse,
    OAuthInitiateRequest,
    OAuthInitiateResponse,
    StoreCredentialRequest,
)
from app.credentials.service import (
    decrypt_to_handle,
    list_credentials,
    revoke_credential,
    store_credential,
)
from app.database import get_session
from app.shared.exceptions import AppError
from app.shared.rate_limit import rate_limit

logger = logging.getLogger("agentnode.credentials.router")

router = APIRouter(prefix="/v1/credentials", tags=["credentials"])


async def _resolve_connector_domains(
    session: AsyncSession, package_slug: str
) -> list[str]:
    """Extract allowed_domains from the connector manifest of a package.

    Reads the manifest_raw JSONB and pulls connector.health_check.endpoint
    and any domain hints. Returns empty list if not a connector package.
    """
    from app.packages.models import Package, PackageVersion

    result = await session.execute(
        select(PackageVersion.manifest_raw)
        .join(Package, Package.id == PackageVersion.package_id)
        .where(Package.slug == package_slug)
        .order_by(PackageVersion.published_at.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if not row:
        return []

    connector = row.get("connector", {}) if isinstance(row, dict) else {}
    domains = []

    # Extract domain from health_check endpoint
    health = connector.get("health_check", {})
    endpoint = health.get("endpoint", "")
    if endpoint:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(endpoint)
            if parsed.hostname:
                domains.append(parsed.hostname)
        except Exception:
            pass

    return domains


async def _resolve_health_endpoint(
    session: AsyncSession, package_slug: str,
) -> str | None:
    """Extract the health_check.endpoint from a connector manifest."""
    from app.packages.models import Package, PackageVersion

    result = await session.execute(
        select(PackageVersion.manifest_raw)
        .join(Package, Package.id == PackageVersion.package_id)
        .where(Package.slug == package_slug)
        .order_by(PackageVersion.published_at.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if not row or not isinstance(row, dict):
        return None

    connector = row.get("connector", {})
    health = connector.get("health_check", {})
    return health.get("endpoint") or None


async def _check_publisher_trust(session: AsyncSession, package_slug: str) -> None:
    """Policy: connector packages must have trust >= verified."""
    from app.packages.models import Package
    from app.publishers.models import Publisher

    result = await session.execute(
        select(Publisher.trust_level)
        .join(Package, Package.publisher_id == Publisher.id)
        .where(Package.slug == package_slug)
    )
    trust = result.scalar_one_or_none()
    if not trust:
        raise AppError("CRED_PACKAGE_NOT_FOUND", f"Package '{package_slug}' not found", 404)

    trust_order = ["unverified", "verified", "trusted", "curated"]
    try:
        level = trust_order.index(trust)
    except ValueError:
        level = -1

    min_level = trust_order.index("verified")
    if level < min_level:
        raise AppError(
            "CRED_TRUST_TOO_LOW",
            f"Credential storage requires publisher trust >= verified "
            f"(got '{trust}')",
            403,
        )


@router.post("/", response_model=CredentialStoreResponse, status_code=201, dependencies=[Depends(rate_limit(10, 60))])
async def store(
    body: StoreCredentialRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Store an encrypted credential for a connector package."""
    # Policy: trust >= verified
    await _check_publisher_trust(session, body.connector_package_slug)

    # Resolve allowed domains from the connector manifest
    allowed_domains = await _resolve_connector_domains(session, body.connector_package_slug)

    cred = await store_credential(
        session,
        user_id=user.id,
        connector_provider=body.connector_provider,
        connector_package_slug=body.connector_package_slug,
        auth_type=body.auth_type,
        secret_data=body.secret_data,
        scopes=body.scopes,
        allowed_domains=allowed_domains,
    )

    return CredentialStoreResponse(
        id=cred.id,
        connector_provider=cred.connector_provider,
        status=cred.status,
        message=f"Credential stored for {cred.connector_provider}",
    )


@router.get("/", response_model=CredentialListResponse, dependencies=[Depends(rate_limit(30, 60))])
async def list_creds(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """List all credentials for the current user. No secrets returned."""
    creds = await list_credentials(session, user.id)
    return CredentialListResponse(
        credentials=[
            CredentialSummary(
                id=c.id,
                connector_provider=c.connector_provider,
                connector_package_slug=c.connector_package_slug,
                auth_type=c.auth_type,
                scopes=c.scopes or [],
                allowed_domains=c.allowed_domains or [],
                status=c.status,
                created_at=c.created_at,
                last_used_at=c.last_used_at,
                expires_at=c.expires_at,
            )
            for c in creds
        ]
    )


@router.delete("/{credential_id}", status_code=204, dependencies=[Depends(rate_limit(10, 60))])
async def revoke(
    credential_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Revoke a credential."""
    from uuid import UUID as _UUID

    cred = await revoke_credential(session, user.id, _UUID(credential_id))
    if not cred:
        raise AppError("CRED_NOT_FOUND", "Credential not found", 404)


@router.post("/{credential_id}/test", response_model=CredentialTestResponse, dependencies=[Depends(rate_limit(5, 60))])
async def test_connectivity(
    credential_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Test credential connectivity by calling the connector's health endpoint."""
    from uuid import UUID as _UUID
    from sqlalchemy import select as sa_select
    from app.credentials.models import CredentialStore

    result = await session.execute(
        sa_select(CredentialStore).where(
            CredentialStore.id == _UUID(credential_id),
            CredentialStore.user_id == user.id,
        )
    )
    cred = result.scalar_one_or_none()
    if not cred:
        raise AppError("CRED_NOT_FOUND", "Credential not found", 404)

    if cred.status != "active":
        return CredentialTestResponse(
            reachable=False,
            message=f"Credential is {cred.status} — cannot test",
        )

    # Resolve health_check endpoint from connector manifest
    health_endpoint = await _resolve_health_endpoint(session, cred.connector_package_slug)
    if not health_endpoint:
        return CredentialTestResponse(
            reachable=True,
            message="Credential is active (no health_check endpoint configured)",
        )

    # Decrypt and build handle
    try:
        allowed_domains = await _resolve_connector_domains(session, cred.connector_package_slug)
        handle = decrypt_to_handle(cred, allowed_domains=allowed_domains)
    except Exception as exc:
        logger.warning("Failed to decrypt credential for health check: %s", exc)
        return CredentialTestResponse(
            reachable=False,
            message="Failed to decrypt credential",
        )

    # Call the health endpoint
    import time as _time
    t0 = _time.monotonic()
    try:
        resp = handle.authorized_request("GET", health_endpoint, timeout=10.0)
        latency = (_time.monotonic() - t0) * 1000
        reachable = 200 <= resp.status_code < 400
        return CredentialTestResponse(
            reachable=reachable,
            status_code=resp.status_code,
            latency_ms=round(latency, 1),
            message="OK" if reachable else f"Health check returned {resp.status_code}",
        )
    except PermissionError as exc:
        return CredentialTestResponse(
            reachable=False,
            message=f"Domain not allowed: {exc}",
        )
    except Exception as exc:
        latency = (_time.monotonic() - t0) * 1000
        return CredentialTestResponse(
            reachable=False,
            latency_ms=round(latency, 1),
            message=f"Connection failed: {type(exc).__name__}",
        )


@router.post("/oauth/initiate", response_model=OAuthInitiateResponse, dependencies=[Depends(rate_limit(5, 60))])
async def oauth_initiate(
    body: OAuthInitiateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Start an OAuth2 PKCE flow for a connector package."""
    await _check_publisher_trust(session, body.connector_package_slug)

    from app.credentials.oauth import generate_auth_url
    result = await generate_auth_url(
        session,
        connector_package_slug=body.connector_package_slug,
        scopes=body.scopes,
        user_id=str(user.id),
    )
    return OAuthInitiateResponse(auth_url=result["auth_url"], state=result["state"])


@router.get("/oauth/callback", dependencies=[Depends(rate_limit(10, 60))])
async def oauth_callback(
    code: str,
    state: str,
    session: AsyncSession = Depends(get_session),
):
    """OAuth2 callback — exchange code for tokens and store encrypted."""
    from fastapi.responses import RedirectResponse
    from app.credentials.oauth import exchange_code

    try:
        result = await exchange_code(session, code=code, state=state)
        return RedirectResponse(
            url=f"/credentials?oauth=success&provider={result['provider']}",
            status_code=302,
        )
    except AppError:
        raise
    except Exception as exc:
        logger.error("OAuth callback failed: %s", exc)
        return RedirectResponse(
            url=f"/credentials?oauth=error&message={type(exc).__name__}",
            status_code=302,
        )


# ---------------------------------------------------------------------------
# PR 5: Vault-SDK Bridge — resolve and proxy endpoints
# ---------------------------------------------------------------------------

@router.get("/resolve/{provider}", dependencies=[Depends(rate_limit(10, 60))])
async def resolve_credential(
    provider: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Resolve a credential for a provider — returns a short-lived resolve_token.

    The actual secret never leaves the server. The SDK uses the resolve_token
    to make proxied requests via POST /v1/credentials/proxy.
    """
    import jwt
    import time as _time

    cred = await _get_active_credential(session, user.id, provider)
    if not cred:
        raise AppError("CRED_NOT_FOUND", f"No active credential for provider '{provider}'", 404)

    # Generate short-lived JWT (60s TTL)
    secret_key = os.environ.get("CREDENTIAL_ENCRYPTION_KEY", "fallback-key")
    resolve_token = jwt.encode(
        {
            "cred_id": str(cred.id),
            "user_id": str(user.id),
            "provider": provider,
            "exp": _time.time() + 60,
        },
        secret_key,
        algorithm="HS256",
    )

    return {
        "resolve_token": resolve_token,
        "provider": provider,
        "allowed_domains": cred.allowed_domains or [],
    }


@router.post("/proxy", dependencies=[Depends(rate_limit(10, 60))])
async def proxy_request(
    body: dict,
    session: AsyncSession = Depends(get_session),
):
    """Proxy an authenticated request through the backend.

    The SDK sends: {resolve_token, method, url, json?}
    The server validates the token, decrypts the credential, makes the
    request with auth headers, and returns {status_code, body}.
    No secrets in the response.
    """
    import jwt

    resolve_token = body.get("resolve_token")
    method = body.get("method", "GET")
    url = body.get("url")
    json_body = body.get("json")

    if not resolve_token or not url:
        raise AppError("PROXY_INVALID", "resolve_token and url are required", 400)

    # Validate token
    secret_key = os.environ.get("CREDENTIAL_ENCRYPTION_KEY", "fallback-key")
    try:
        payload = jwt.decode(resolve_token, secret_key, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise AppError("PROXY_TOKEN_EXPIRED", "Resolve token has expired", 401)
    except jwt.InvalidTokenError:
        raise AppError("PROXY_TOKEN_INVALID", "Invalid resolve token", 401)

    # Load credential
    from uuid import UUID as _UUID
    from sqlalchemy import select as sa_select
    from app.credentials.models import CredentialStore

    cred_id = _UUID(payload["cred_id"])
    result = await session.execute(
        sa_select(CredentialStore).where(
            CredentialStore.id == cred_id,
            CredentialStore.status == "active",
        )
    )
    cred = result.scalar_one_or_none()
    if not cred:
        raise AppError("PROXY_CRED_REVOKED", "Credential has been revoked", 404)

    # Domain validation
    allowed_domains = cred.allowed_domains or []
    if allowed_domains:
        from urllib.parse import urlparse
        host = urlparse(url).hostname or ""
        if host.lower() not in [d.lower() for d in allowed_domains]:
            raise AppError(
                "PROXY_DOMAIN_DENIED",
                f"Domain '{host}' not in allowed domains",
                403,
            )

    # Decrypt and make the request
    handle = decrypt_to_handle(cred)
    try:
        resp = handle.authorized_request(method, url, json=json_body, timeout=15.0)
        return {"status_code": resp.status_code, "body": resp.body}
    except PermissionError as exc:
        raise AppError("PROXY_DOMAIN_DENIED", str(exc), 403)
    except Exception as exc:
        raise AppError("PROXY_REQUEST_FAILED", f"Proxy request failed: {type(exc).__name__}", 502)


async def _get_active_credential(session: AsyncSession, user_id, provider: str):
    """Get active credential for user + provider."""
    from sqlalchemy import select as sa_select
    from app.credentials.models import CredentialStore

    result = await session.execute(
        sa_select(CredentialStore).where(
            CredentialStore.user_id == user_id,
            CredentialStore.connector_provider == provider,
            CredentialStore.status == "active",
        )
    )
    return result.scalar_one_or_none()
