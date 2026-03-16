from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
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
from app.packages.models import CapabilityTaxonomy, Package, PackageReport, PackageVersion
from app.packages.version_queries import recalculate_latest_version_id
from app.publishers.models import Publisher
from app.shared.exceptions import AppError
from app.webhooks.service import fire_event

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

    await fire_event(session, pkg.publisher_id, "version.quarantined", {"slug": slug, "version": version, "reason": body.reason})

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

    await fire_event(session, pkg.publisher_id, "version.cleared", {"slug": slug, "version": version})

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

    await fire_event(session, pkg.publisher_id, "version.rejected", {"slug": slug, "version": version})

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


# --- Capability Taxonomy CRUD ---


class CreateCapabilityRequest(BaseModel):
    id: str = Field(..., pattern=r"^[a-z0-9_.]+$")
    display_name: str
    description: str | None = None
    category: str | None = None


class UpdateCapabilityRequest(BaseModel):
    display_name: str | None = None
    description: str | None = None
    category: str | None = None


@router.get("/capabilities")
async def list_capabilities(
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """List all capability taxonomy entries."""
    result = await session.execute(
        select(CapabilityTaxonomy).order_by(CapabilityTaxonomy.category, CapabilityTaxonomy.id)
    )
    caps = result.scalars().all()
    return {
        "capabilities": [
            {
                "id": c.id,
                "display_name": c.display_name,
                "description": c.description,
                "category": c.category,
            }
            for c in caps
        ],
        "total": len(caps),
    }


@router.post("/capabilities", status_code=201)
async def create_capability(
    body: CreateCapabilityRequest,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Create a new capability in the taxonomy."""
    existing = await session.execute(
        select(CapabilityTaxonomy).where(CapabilityTaxonomy.id == body.id)
    )
    if existing.scalar_one_or_none():
        raise AppError("CAPABILITY_EXISTS", f"Capability '{body.id}' already exists", 409)

    cap = CapabilityTaxonomy(
        id=body.id,
        display_name=body.display_name,
        description=body.description,
        category=body.category,
    )
    session.add(cap)
    await session.commit()

    return {"id": cap.id, "display_name": cap.display_name, "category": cap.category}


@router.put("/capabilities/{cap_id}")
async def update_capability(
    cap_id: str,
    body: UpdateCapabilityRequest,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Update a capability in the taxonomy."""
    result = await session.execute(
        select(CapabilityTaxonomy).where(CapabilityTaxonomy.id == cap_id)
    )
    cap = result.scalar_one_or_none()
    if not cap:
        raise AppError("CAPABILITY_NOT_FOUND", f"Capability '{cap_id}' not found", 404)

    if body.display_name is not None:
        cap.display_name = body.display_name
    if body.description is not None:
        cap.description = body.description
    if body.category is not None:
        cap.category = body.category

    await session.commit()

    return {"id": cap.id, "display_name": cap.display_name, "category": cap.category}


@router.delete("/capabilities/{cap_id}")
async def delete_capability(
    cap_id: str,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Delete a capability from the taxonomy."""
    result = await session.execute(
        select(CapabilityTaxonomy).where(CapabilityTaxonomy.id == cap_id)
    )
    cap = result.scalar_one_or_none()
    if not cap:
        raise AppError("CAPABILITY_NOT_FOUND", f"Capability '{cap_id}' not found", 404)

    await session.delete(cap)
    await session.commit()

    return {"deleted": True}


# --- Report Management ---


@router.get("/reports")
async def list_reports(
    status: str | None = None,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """List package reports. Optionally filter by status."""
    query = select(PackageReport).order_by(PackageReport.created_at.desc()).limit(100)
    if status:
        query = query.where(PackageReport.status == status)

    result = await session.execute(query)
    reports = result.scalars().all()

    return {
        "reports": [
            {
                "id": str(r.id),
                "package_id": str(r.package_id),
                "reporter_user_id": str(r.reporter_user_id),
                "reason": r.reason,
                "description": r.description,
                "status": r.status,
                "resolution_note": r.resolution_note,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
            }
            for r in reports
        ],
        "total": len(reports),
    }


class ResolveReportRequest(BaseModel):
    status: str = Field(..., pattern=r"^(resolved|dismissed)$")
    resolution_note: str | None = None


@router.post("/reports/{report_id}/resolve")
async def resolve_report(
    report_id: str,
    body: ResolveReportRequest,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Resolve or dismiss a report."""
    from uuid import UUID
    result = await session.execute(
        select(PackageReport).where(PackageReport.id == UUID(report_id))
    )
    report = result.scalar_one_or_none()
    if not report:
        raise AppError("REPORT_NOT_FOUND", "Report not found", 404)

    report.status = body.status
    report.resolution_note = body.resolution_note
    report.resolved_by = user.id
    report.resolved_at = datetime.now(timezone.utc)
    await session.commit()

    return {"resolved": True, "status": body.status}


# --- GET /v1/admin/stats (Observability) ---

@router.get("/stats")
async def get_platform_stats(
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Platform-wide observability stats."""
    from sqlalchemy import func
    from app.packages.models import Installation

    # Package counts
    pkg_count = await session.execute(select(func.count(Package.id)))
    total_packages = pkg_count.scalar() or 0

    # Version counts
    ver_count = await session.execute(select(func.count(PackageVersion.id)))
    total_versions = ver_count.scalar() or 0

    # Total downloads
    dl_sum = await session.execute(select(func.sum(Package.download_count)))
    total_downloads = dl_sum.scalar() or 0

    # Installation stats
    install_total = await session.execute(select(func.count(Installation.id)))
    total_installs = install_total.scalar() or 0

    active_installs = await session.execute(
        select(func.count(Installation.id)).where(Installation.status == "active")
    )
    total_active = active_installs.scalar() or 0

    failed_installs = await session.execute(
        select(func.count(Installation.id)).where(Installation.status == "failed")
    )
    total_failed = failed_installs.scalar() or 0

    # Publisher counts
    pub_count = await session.execute(select(func.count(Publisher.id)))
    total_publishers = pub_count.scalar() or 0

    suspended_count = await session.execute(
        select(func.count(Publisher.id)).where(Publisher.is_suspended == True)
    )
    total_suspended = suspended_count.scalar() or 0

    # Quarantined versions
    quarantined_count = await session.execute(
        select(func.count(PackageVersion.id)).where(PackageVersion.quarantine_status == "quarantined")
    )
    total_quarantined = quarantined_count.scalar() or 0

    # Open reports
    open_reports = await session.execute(
        select(func.count(PackageReport.id)).where(PackageReport.status == "submitted")
    )
    total_open_reports = open_reports.scalar() or 0

    # Top packages by downloads
    top_packages_result = await session.execute(
        select(Package.slug, Package.download_count)
        .order_by(Package.download_count.desc())
        .limit(10)
    )
    top_packages = [{"slug": row[0], "downloads": row[1]} for row in top_packages_result.all()]

    return {
        "packages": {
            "total": total_packages,
            "total_versions": total_versions,
            "quarantined": total_quarantined,
        },
        "downloads": {
            "total": total_downloads,
            "top_packages": top_packages,
        },
        "installations": {
            "total": total_installs,
            "active": total_active,
            "failed": total_failed,
        },
        "publishers": {
            "total": total_publishers,
            "suspended": total_suspended,
        },
        "moderation": {
            "open_reports": total_open_reports,
        },
    }
