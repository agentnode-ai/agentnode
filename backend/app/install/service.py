"""Install service — assembles install metadata and tracks downloads."""
import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.packages.models import Installation, Package, PackageVersion
from app.packages.version_queries import InstallResolution, get_latest_installable_version
from app.shared.exceptions import AppError
from app.shared.storage import generate_presigned_url

DOWNLOAD_DEDUP_TTL = 3600  # 1 hour

logger = logging.getLogger(__name__)


async def get_install_version(
    session: AsyncSession, slug: str, version: str | None = None,
) -> tuple[Package, PackageVersion, str]:
    """Load a package and its target version for install.

    Returns (package, version, install_resolution).
    """
    result = await session.execute(
        select(Package).where(Package.slug == slug)
    )
    pkg = result.scalar_one_or_none()
    if not pkg:
        raise AppError("PACKAGE_NOT_FOUND", f"Package '{slug}' not found", 404)
    # Note: deprecated packages ARE installable per spec §12.2
    # The caller should check pkg.is_deprecated and include a warning in the response

    if version:
        ver_result = await session.execute(
            select(PackageVersion)
            .options(
                selectinload(PackageVersion.capabilities),
                selectinload(PackageVersion.dependencies),
                selectinload(PackageVersion.permissions),
            )
            .where(
                PackageVersion.package_id == pkg.id,
                PackageVersion.version_number == version,
            )
        )
        pv = ver_result.scalar_one_or_none()
        if not pv:
            raise AppError("VERSION_NOT_FOUND", f"Version '{version}' not found", 404)
        reason = InstallResolution.PINNED
    else:
        pv, reason = await get_latest_installable_version(
            session, pkg.id,
            options=[
                selectinload(PackageVersion.capabilities),
                selectinload(PackageVersion.dependencies),
                selectinload(PackageVersion.permissions),
            ],
        )
        if not pv:
            raise AppError("NO_VERSION_AVAILABLE", "No installable version available", 404)

    if pv.is_yanked:
        raise AppError("VERSION_YANKED", f"Version '{pv.version_number}' has been yanked", 410)
    if pv.quarantine_status not in ("none", "cleared"):
        raise AppError("VERSION_QUARANTINED", "This version is under quarantine", 403)

    return pkg, pv, reason


async def build_artifact_info(pv: PackageVersion) -> dict | None:
    """Build presigned download URL for artifact if available."""
    if not pv.artifact_object_key:
        return None

    url = await generate_presigned_url(pv.artifact_object_key, expires_in=900)
    return {
        "url": url,
        "hash_sha256": pv.artifact_hash_sha256,
        "size_bytes": pv.artifact_size_bytes,
        "expires_in_seconds": 900,
    }


async def create_installation(
    session: AsyncSession,
    user_id: UUID,
    package_id: UUID,
    version_id: UUID,
    source: str = "cli",
    event_type: str = "install",
    context: dict | None = None,
) -> UUID:
    """Create an installation record. Returns installation ID."""
    inst = Installation(
        user_id=user_id,
        package_id=package_id,
        package_version_id=version_id,
        source=source,
        status="installed",
        event_type=event_type,
        installation_context=context or {},
    )
    session.add(inst)
    await session.flush()
    return inst.id


async def track_download(session: AsyncSession, package_id, version_id, *, redis=None, dedup_key: str | None = None) -> int:
    """Increment download counter and return new count.

    When *redis* and *dedup_key* are provided the counter is only bumped
    once per dedup_key within DOWNLOAD_DEDUP_TTL seconds.  Repeated calls
    within the window return the current count without incrementing.

    Does NOT commit — the caller controls the transaction boundary.
    """
    if redis and dedup_key:
        redis_key = f"download:{package_id}:{dedup_key}"
        # SET NX returns True if the key was created (first download in window)
        is_new = await redis.set(redis_key, "1", ex=DOWNLOAD_DEDUP_TTL, nx=True)
        if not is_new:
            # Duplicate within the dedup window — return current count only
            result = await session.execute(
                select(Package.download_count).where(Package.id == package_id)
            )
            return result.scalar_one()

    result = await session.execute(
        update(Package)
        .where(Package.id == package_id)
        .values(download_count=Package.download_count + 1)
        .returning(Package.download_count)
    )
    new_count = result.scalar_one()
    return new_count
