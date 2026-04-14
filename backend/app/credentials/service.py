"""Credential business logic — encrypt, store, list, revoke.

Security invariants (S3, S8, S12):
- Raw secrets are encrypted before hitting the DB
- Secrets never appear in exceptions, logs, or API responses
- Domain restrictions are copied from the connector manifest at store time
"""
from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials.models import CredentialStore
from app.credentials import vault

logger = logging.getLogger("agentnode.credentials.service")


async def store_credential(
    session: AsyncSession,
    *,
    user_id: UUID,
    connector_provider: str,
    connector_package_slug: str,
    auth_type: str,
    secret_data: dict,
    scopes: list[str],
    allowed_domains: list[str],
) -> CredentialStore:
    """Encrypt and store a credential. Upserts on (user_id, connector_provider)."""
    if auth_type not in ("api_key", "oauth2"):
        raise ValueError(f"Unsupported auth_type: {auth_type}")

    encrypted = vault.encrypt(secret_data)

    # Check for existing credential for this user + provider
    result = await session.execute(
        select(CredentialStore).where(
            CredentialStore.user_id == user_id,
            CredentialStore.connector_provider == connector_provider,
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.encrypted_data = encrypted
        existing.auth_type = auth_type
        existing.scopes = scopes
        existing.allowed_domains = allowed_domains
        existing.connector_package_slug = connector_package_slug
        existing.status = "active"
        await session.commit()
        await session.refresh(existing)
        logger.info(
            "Updated credential for user=%s provider=%s",
            user_id, connector_provider,
        )
        return existing

    cred = CredentialStore(
        user_id=user_id,
        connector_provider=connector_provider,
        connector_package_slug=connector_package_slug,
        auth_type=auth_type,
        encrypted_data=encrypted,
        scopes=scopes,
        allowed_domains=allowed_domains,
        status="active",
    )
    session.add(cred)
    await session.commit()
    await session.refresh(cred)
    logger.info(
        "Stored credential for user=%s provider=%s",
        user_id, connector_provider,
    )
    return cred


async def list_credentials(
    session: AsyncSession,
    user_id: UUID,
) -> list[CredentialStore]:
    """List all credentials for a user. Never returns encrypted data."""
    result = await session.execute(
        select(CredentialStore)
        .where(CredentialStore.user_id == user_id)
        .order_by(CredentialStore.created_at.desc())
    )
    return list(result.scalars().all())


async def revoke_credential(
    session: AsyncSession,
    user_id: UUID,
    credential_id: UUID,
) -> CredentialStore | None:
    """Revoke a credential. Returns None if not found or not owned by user."""
    result = await session.execute(
        select(CredentialStore).where(
            CredentialStore.id == credential_id,
            CredentialStore.user_id == user_id,
        )
    )
    cred = result.scalar_one_or_none()
    if not cred:
        return None

    cred.status = "revoked"
    await session.commit()
    await session.refresh(cred)
    logger.info(
        "Revoked credential id=%s provider=%s user=%s",
        credential_id, cred.connector_provider, user_id,
    )
    return cred


async def get_credential_for_provider(
    session: AsyncSession,
    user_id: UUID,
    connector_provider: str,
) -> CredentialStore | None:
    """Get active credential for a specific provider. Used by runtime."""
    result = await session.execute(
        select(CredentialStore).where(
            CredentialStore.user_id == user_id,
            CredentialStore.connector_provider == connector_provider,
            CredentialStore.status == "active",
        )
    )
    return result.scalar_one_or_none()


def decrypt_to_handle(cred: CredentialStore, allowed_domains: list[str] | None = None):
    """Decrypt a CredentialStore and return a CredentialHandle.

    Internal only — the handle is never returned in an API response.
    """
    from agentnode_sdk.credential_handle import CredentialHandle

    secret_data = vault.decrypt(cred.encrypted_data)
    return CredentialHandle(
        provider=cred.connector_provider,
        auth_type=cred.auth_type,
        scopes=cred.scopes or [],
        allowed_domains=allowed_domains or cred.allowed_domains or [],
        secret_data=secret_data,
    )
