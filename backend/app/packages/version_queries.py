"""Shared version selection helpers — section 4.2 of spec.

All version visibility logic MUST go through these helpers.
"""
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.packages.models import Package, PackageVersion


async def get_public_versions(session: AsyncSession, package_id: UUID) -> list[PackageVersion]:
    """Returns stable/beta versions WHERE quarantine_status IN ('none','cleared')
    AND is_yanked = false, ordered by published_at DESC."""
    result = await session.execute(
        select(PackageVersion)
        .where(
            PackageVersion.package_id == package_id,
            PackageVersion.quarantine_status.in_(("none", "cleared")),
            PackageVersion.is_yanked == False,  # noqa: E712
        )
        .order_by(PackageVersion.published_at.desc())
    )
    return list(result.scalars().all())


async def get_owner_visible_versions(session: AsyncSession, package_id: UUID) -> list[PackageVersion]:
    """Returns ALL versions including quarantined/yanked, ordered by published_at DESC."""
    result = await session.execute(
        select(PackageVersion)
        .where(PackageVersion.package_id == package_id)
        .order_by(PackageVersion.published_at.desc())
    )
    return list(result.scalars().all())


async def get_latest_public_version(session: AsyncSession, package_id: UUID) -> PackageVersion | None:
    """Returns newest stable public version, or None."""
    result = await session.execute(
        select(PackageVersion)
        .where(
            PackageVersion.package_id == package_id,
            PackageVersion.channel == "stable",
            PackageVersion.quarantine_status.in_(("none", "cleared")),
            PackageVersion.is_yanked == False,  # noqa: E712
        )
        .order_by(PackageVersion.published_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def recalculate_latest_version_id(session: AsyncSession, package_id: UUID) -> None:
    """Queries get_latest_public_version and sets packages.latest_version_id accordingly.
    Sets to NULL if no qualifying version exists.
    MUST be called in SAME transaction as publish/yank/deprecate/quarantine-clear/reject.
    """
    latest = await get_latest_public_version(session, package_id)
    result = await session.execute(
        select(Package).where(Package.id == package_id)
    )
    pkg = result.scalar_one_or_none()
    if pkg:
        pkg.latest_version_id = latest.id if latest else None
