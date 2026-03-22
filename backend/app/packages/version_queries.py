"""Shared version selection helpers — section 4.2 of spec.

All version visibility logic MUST go through these helpers.
"""
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import and_, case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.packages.models import Package, PackageVersion


class InstallResolution:
    VERIFIED = "verified"
    PARTIAL = "partial"
    PENDING = "pending"
    FALLBACK = "fallback"
    PINNED = "pinned"


# Central priority mapping: (priority_int, resolution_string)
# Used by both the SQL CASE expression and the Python reason derivation.
# If you change priorities here, both paths update automatically.
TIER_PRIORITY = {
    1: InstallResolution.VERIFIED,   # gold, verified
    2: InstallResolution.PARTIAL,    # partial
    3: InstallResolution.PENDING,    # pending + recent (<24h)
    4: InstallResolution.FALLBACK,   # everything else
}


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


async def get_latest_owner_visible_version(session: AsyncSession, package_id: UUID) -> PackageVersion | None:
    """Latest non-yanked owner-visible version, including quarantined. Used for owner edit + re-verify."""
    result = await session.execute(
        select(PackageVersion)
        .where(
            PackageVersion.package_id == package_id,
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


def _tier_priority_for_version(pv: PackageVersion) -> int:
    """Return the priority bucket (1-4) for a version. Single source of truth.

    This mirrors the SQL CASE in get_latest_installable_version().
    Both MUST use the same rules — change here, change there.
    """
    if pv.verification_tier in ("gold", "verified"):
        return 1
    if pv.verification_tier == "partial":
        return 2
    recent_cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    if pv.verification_status == "pending" and pv.published_at and pv.published_at >= recent_cutoff:
        return 3
    return 4


def _derive_install_reason(pv: PackageVersion) -> str:
    """Derive InstallResolution reason from a version's verification state.

    Uses TIER_PRIORITY central mapping to stay in sync with the SQL CASE.
    """
    return TIER_PRIORITY[_tier_priority_for_version(pv)]


async def get_latest_installable_version(
    session: AsyncSession,
    package_id: UUID,
    options: list | None = None,
) -> tuple[PackageVersion | None, str]:
    """Returns (version, reason) where reason is an InstallResolution constant.

    Priority ordering:
    1. verification_tier IN ('gold', 'verified') → VERIFIED
    2. verification_tier = 'partial' → PARTIAL
    3. verification_status = 'pending' AND published_at within 24h → PENDING
    4. Everything else → FALLBACK
    """
    recent_cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    tier_priority = case(
        (PackageVersion.verification_tier.in_(("gold", "verified")), 1),
        (PackageVersion.verification_tier == "partial", 2),
        (and_(
            PackageVersion.verification_status == "pending",
            PackageVersion.published_at >= recent_cutoff,
        ), 3),
        else_=4,
    )

    query = (
        select(PackageVersion)
        .where(
            PackageVersion.package_id == package_id,
            PackageVersion.channel == "stable",
            PackageVersion.quarantine_status.in_(("none", "cleared")),
            PackageVersion.is_yanked == False,  # noqa: E712
        )
        .order_by(tier_priority.asc(), PackageVersion.published_at.desc())
        .limit(1)
    )

    if options:
        query = query.options(*options)

    result = await session.execute(query)
    pv = result.scalar_one_or_none()
    if not pv:
        return None, InstallResolution.FALLBACK

    return pv, _derive_install_reason(pv)
