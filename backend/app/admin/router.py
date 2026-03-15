from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.admin.schemas import (
    QuarantineActionResponse,
    QuarantinedVersionItem,
    QuarantineVersionRequest,
    SetTrustLevelRequest,
    SuspendedPublisherItem,
    SuspendPublisherRequest,
    SuspensionResponse,
    TrustLevelResponse,
)
from app.auth.dependencies import require_admin
from app.auth.models import User
from app.database import get_session
from app.packages.models import Package, PackageVersion
from app.packages.version_queries import recalculate_latest_version_id
from app.publishers.models import Publisher
from app.shared.exceptions import AppError

router = APIRouter(prefix="/v1/admin", tags=["admin"])


# --- Quarantine endpoints ---


@router.post("/packages/{slug}/versions/{version}/quarantine", response_model=QuarantineActionResponse)
async def quarantine_version(
    slug: str,
    version: str,
    body: QuarantineVersionRequest,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Quarantine a package version."""
    pkg, pv = await _get_package_version(session, slug, version)

    pv.quarantine_status = "quarantined"
    pv.quarantined_at = datetime.now(timezone.utc)
    pv.quarantine_reason = body.reason

    await recalculate_latest_version_id(session, pkg.id)
    await session.commit()

    return QuarantineActionResponse(
        slug=slug, version=version,
        quarantine_status="quarantined",
        message=f"Version {slug}@{version} quarantined: {body.reason}",
    )


@router.post("/packages/{slug}/versions/{version}/clear", response_model=QuarantineActionResponse)
async def clear_quarantine(
    slug: str,
    version: str,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Clear quarantine on a package version."""
    pkg, pv = await _get_package_version(session, slug, version)

    if pv.quarantine_status != "quarantined":
        raise AppError("NOT_QUARANTINED", "Version is not quarantined", 409)

    pv.quarantine_status = "cleared"
    pv.quarantine_reason = None

    await recalculate_latest_version_id(session, pkg.id)
    await session.commit()

    return QuarantineActionResponse(
        slug=slug, version=version,
        quarantine_status="cleared",
        message=f"Quarantine cleared for {slug}@{version}",
    )


@router.post("/packages/{slug}/versions/{version}/reject", response_model=QuarantineActionResponse)
async def reject_version(
    slug: str,
    version: str,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Reject a quarantined package version."""
    pkg, pv = await _get_package_version(session, slug, version)

    if pv.quarantine_status != "quarantined":
        raise AppError("NOT_QUARANTINED", "Version is not quarantined", 409)

    pv.quarantine_status = "rejected"

    await recalculate_latest_version_id(session, pkg.id)
    await session.commit()

    return QuarantineActionResponse(
        slug=slug, version=version,
        quarantine_status="rejected",
        message=f"Version {slug}@{version} rejected",
    )


@router.get("/quarantined", response_model=list[QuarantinedVersionItem])
async def list_quarantined(
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """List all quarantined versions."""
    result = await session.execute(
        select(PackageVersion)
        .options(selectinload(PackageVersion.package))
        .where(PackageVersion.quarantine_status == "quarantined")
        .order_by(PackageVersion.quarantined_at.desc())
    )
    versions = result.scalars().all()

    return [
        QuarantinedVersionItem(
            package_slug=v.package.slug,
            version_number=v.version_number,
            quarantine_status=v.quarantine_status,
            quarantined_at=v.quarantined_at,
            quarantine_reason=v.quarantine_reason,
        )
        for v in versions
    ]


# --- Trust level endpoints ---


@router.put("/publishers/{slug}/trust", response_model=TrustLevelResponse)
async def set_trust_level(
    slug: str,
    body: SetTrustLevelRequest,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Set a publisher's trust level."""
    pub = await _get_publisher(session, slug)
    pub.trust_level = body.trust_level
    await session.commit()

    return TrustLevelResponse(
        publisher_slug=slug,
        trust_level=body.trust_level,
        message=f"Publisher '{slug}' trust level set to '{body.trust_level}'",
    )


# --- Suspension endpoints ---


@router.post("/publishers/{slug}/suspend", response_model=SuspensionResponse)
async def suspend_publisher(
    slug: str,
    body: SuspendPublisherRequest,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Suspend a publisher."""
    pub = await _get_publisher(session, slug)

    if pub.is_suspended:
        raise AppError("ALREADY_SUSPENDED", "Publisher is already suspended", 409)

    pub.is_suspended = True
    pub.suspension_reason = body.reason
    await session.commit()

    return SuspensionResponse(
        publisher_slug=slug,
        is_suspended=True,
        suspension_reason=body.reason,
        message=f"Publisher '{slug}' has been suspended",
    )


@router.post("/publishers/{slug}/unsuspend", response_model=SuspensionResponse)
async def unsuspend_publisher(
    slug: str,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Unsuspend a publisher."""
    pub = await _get_publisher(session, slug)

    if not pub.is_suspended:
        raise AppError("NOT_SUSPENDED", "Publisher is not suspended", 409)

    pub.is_suspended = False
    pub.suspension_reason = None
    await session.commit()

    return SuspensionResponse(
        publisher_slug=slug,
        is_suspended=False,
        suspension_reason=None,
        message=f"Publisher '{slug}' has been unsuspended",
    )


@router.get("/publishers/suspended", response_model=list[SuspendedPublisherItem])
async def list_suspended(
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """List all suspended publishers."""
    result = await session.execute(
        select(Publisher).where(Publisher.is_suspended == True)  # noqa: E712
    )
    publishers = result.scalars().all()

    return [
        SuspendedPublisherItem(
            slug=p.slug,
            display_name=p.display_name,
            is_suspended=p.is_suspended,
            suspension_reason=p.suspension_reason,
            trust_level=p.trust_level,
        )
        for p in publishers
    ]


# --- Helpers ---


async def _get_package_version(session: AsyncSession, slug: str, version: str) -> tuple[Package, PackageVersion]:
    result = await session.execute(select(Package).where(Package.slug == slug))
    pkg = result.scalar_one_or_none()
    if not pkg:
        raise AppError("PACKAGE_NOT_FOUND", f"Package '{slug}' not found", 404)

    ver_result = await session.execute(
        select(PackageVersion).where(
            PackageVersion.package_id == pkg.id,
            PackageVersion.version_number == version,
        )
    )
    pv = ver_result.scalar_one_or_none()
    if not pv:
        raise AppError("VERSION_NOT_FOUND", f"Version '{version}' not found", 404)

    return pkg, pv


async def _get_publisher(session: AsyncSession, slug: str) -> Publisher:
    result = await session.execute(select(Publisher).where(Publisher.slug == slug))
    pub = result.scalar_one_or_none()
    if not pub:
        raise AppError("PUBLISHER_NOT_FOUND", f"Publisher '{slug}' not found", 404)
    return pub
