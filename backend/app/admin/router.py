from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.admin.models import AdminAuditLog, SystemSetting
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
from app.shared.rate_limit import rate_limit
from app.packages.models import CapabilityTaxonomy, Installation, Package, PackageReport, PackageVersion
from app.packages.version_queries import recalculate_latest_version_id
from app.publishers.models import Publisher
from app.shared.exceptions import AppError
from app.webhooks.service import fire_event


# --- Audit helper ---

async def _audit(
    session: AsyncSession, request: Request, admin: User,
    action: str, target_type: str, target_id: str, metadata: dict | None = None,
) -> None:
    """Log an admin action."""
    forwarded = request.headers.get("x-forwarded-for")
    ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else None)
    log = AdminAuditLog(
        admin_user_id=admin.id,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        metadata_=metadata or {},
        ip_address=ip,
        user_agent=request.headers.get("user-agent"),
    )
    session.add(log)
    await session.flush()

router = APIRouter(prefix="/v1/admin", tags=["admin"])


# --- Quarantine endpoints ---


@router.post("/packages/{slug}/versions/{version}/quarantine", response_model=QuarantineActionResponse, dependencies=[Depends(rate_limit(10, 60))])
async def quarantine_version(
    slug: str,
    version: str,
    body: QuarantineVersionRequest,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Quarantine a package version."""
    pkg, pv = await _get_package_version(session, slug, version)

    pv.quarantine_status = "quarantined"
    pv.quarantined_at = datetime.now(timezone.utc)
    pv.quarantine_reason = body.reason

    await recalculate_latest_version_id(session, pkg.id)
    await _audit(session, request, user, "quarantine_version", "package", slug, {"version": version, "reason": body.reason})
    await session.commit()

    await fire_event(session, pkg.publisher_id, "version.quarantined", {"slug": slug, "version": version, "reason": body.reason})

    from app.shared.email import send_quarantine_email, get_publisher_email
    pub_email = await get_publisher_email(pkg.publisher_id)
    if pub_email:
        await send_quarantine_email(pub_email, slug, version, body.reason)

    return QuarantineActionResponse(
        slug=slug, version=version,
        quarantine_status="quarantined",
        message=f"Version {slug}@{version} quarantined: {body.reason}",
    )


@router.post("/packages/{slug}/versions/{version}/clear", response_model=QuarantineActionResponse, dependencies=[Depends(rate_limit(10, 60))])
async def clear_quarantine(
    slug: str,
    version: str,
    request: Request,
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
    await _audit(session, request, user, "clear_quarantine", "package", slug, {"version": version})
    await session.commit()

    await fire_event(session, pkg.publisher_id, "version.cleared", {"slug": slug, "version": version})

    from app.shared.email import send_quarantine_cleared_email, get_publisher_email
    pub_email = await get_publisher_email(pkg.publisher_id)
    if pub_email:
        await send_quarantine_cleared_email(pub_email, slug, version)

    return QuarantineActionResponse(
        slug=slug, version=version,
        quarantine_status="cleared",
        message=f"Quarantine cleared for {slug}@{version}",
    )


@router.post("/packages/{slug}/versions/{version}/reject", response_model=QuarantineActionResponse, dependencies=[Depends(rate_limit(10, 60))])
async def reject_version(
    slug: str,
    version: str,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Reject a quarantined package version."""
    pkg, pv = await _get_package_version(session, slug, version)

    if pv.quarantine_status != "quarantined":
        raise AppError("NOT_QUARANTINED", "Version is not quarantined", 409)

    pv.quarantine_status = "rejected"

    await recalculate_latest_version_id(session, pkg.id)
    await _audit(session, request, user, "reject_version", "package", slug, {"version": version})
    await session.commit()

    await fire_event(session, pkg.publisher_id, "version.rejected", {"slug": slug, "version": version})

    from app.shared.email import send_version_rejected_email, get_publisher_email
    pub_email = await get_publisher_email(pkg.publisher_id)
    if pub_email:
        await send_version_rejected_email(pub_email, slug, version)

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


@router.put("/publishers/{slug}/trust", response_model=TrustLevelResponse, dependencies=[Depends(rate_limit(10, 60))])
async def set_trust_level(
    slug: str,
    body: SetTrustLevelRequest,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Set a publisher's trust level."""
    pub = await _get_publisher(session, slug)
    old_level = pub.trust_level
    pub.trust_level = body.trust_level
    await _audit(session, request, user, "set_trust_level", "publisher", slug, {"old": old_level, "new": body.trust_level})
    await session.commit()

    from app.shared.email import send_trust_level_changed_email, get_publisher_email
    pub_email = await get_publisher_email(pub.id)
    if pub_email:
        await send_trust_level_changed_email(pub_email, slug, old_level, body.trust_level)

    return TrustLevelResponse(
        publisher_slug=slug,
        trust_level=body.trust_level,
        message=f"Publisher '{slug}' trust level set to '{body.trust_level}'",
    )


# --- Suspension endpoints ---


@router.post("/publishers/{slug}/suspend", response_model=SuspensionResponse, dependencies=[Depends(rate_limit(10, 60))])
async def suspend_publisher(
    slug: str,
    body: SuspendPublisherRequest,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Suspend a publisher."""
    pub = await _get_publisher(session, slug)

    if pub.is_suspended:
        raise AppError("ALREADY_SUSPENDED", "Publisher is already suspended", 409)

    pub.is_suspended = True
    pub.suspension_reason = body.reason
    await _audit(session, request, user, "suspend_publisher", "publisher", slug, {"reason": body.reason})
    await session.commit()

    from app.shared.email import send_publisher_suspended_email, get_publisher_email
    pub_email = await get_publisher_email(pub.id)
    if pub_email:
        await send_publisher_suspended_email(pub_email, slug, body.reason)

    return SuspensionResponse(
        publisher_slug=slug,
        is_suspended=True,
        suspension_reason=body.reason,
        message=f"Publisher '{slug}' has been suspended",
    )


@router.post("/publishers/{slug}/unsuspend", response_model=SuspensionResponse, dependencies=[Depends(rate_limit(10, 60))])
async def unsuspend_publisher(
    slug: str,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Unsuspend a publisher."""
    pub = await _get_publisher(session, slug)

    if not pub.is_suspended:
        raise AppError("NOT_SUSPENDED", "Publisher is not suspended", 409)

    pub.is_suspended = False
    pub.suspension_reason = None
    await _audit(session, request, user, "unsuspend_publisher", "publisher", slug)
    await session.commit()

    from app.shared.email import send_publisher_unsuspended_email, get_publisher_email
    pub_email = await get_publisher_email(pub.id)
    if pub_email:
        await send_publisher_unsuspended_email(pub_email, slug)

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
    """List package reports with readable package slug and reporter username."""
    # Aliases for the reporter user
    reporter = User.__table__.alias("reporter")

    query = (
        select(
            PackageReport,
            Package.slug.label("package_slug"),
            Package.name.label("package_name"),
            reporter.c.username.label("reporter_username"),
        )
        .outerjoin(Package, PackageReport.package_id == Package.id)
        .outerjoin(reporter, PackageReport.reporter_user_id == reporter.c.id)
        .order_by(PackageReport.created_at.desc())
        .limit(100)
    )
    if status:
        query = query.where(PackageReport.status == status)

    result = await session.execute(query)
    rows = result.all()

    return {
        "reports": [
            {
                "id": str(r.PackageReport.id),
                "package_id": str(r.PackageReport.package_id),
                "package_slug": r.package_slug,
                "package_name": r.package_name,
                "reporter_user_id": str(r.PackageReport.reporter_user_id),
                "reporter_username": r.reporter_username,
                "reason": r.PackageReport.reason,
                "description": r.PackageReport.description,
                "status": r.PackageReport.status,
                "resolution_note": r.PackageReport.resolution_note,
                "created_at": r.PackageReport.created_at.isoformat() if r.PackageReport.created_at else None,
                "resolved_at": r.PackageReport.resolved_at.isoformat() if r.PackageReport.resolved_at else None,
            }
            for r in rows
        ],
        "total": len(rows),
    }


class ResolveReportRequest(BaseModel):
    status: str = Field(..., pattern=r"^(resolved|dismissed)$")
    resolution_note: str | None = None


@router.post("/reports/{report_id}/resolve", dependencies=[Depends(rate_limit(10, 60))])
async def resolve_report(
    report_id: str,
    body: ResolveReportRequest,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Resolve or dismiss a report."""
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
    await _audit(session, request, user, "resolve_report", "report", report_id, {"status": body.status})
    await session.commit()

    # Notify reporter
    from app.shared.email import send_report_resolved_reporter_email
    reporter = await session.execute(select(User).where(User.id == report.reporter_user_id))
    reporter_user = reporter.scalar_one_or_none()
    pkg_result = await session.execute(select(Package).where(Package.id == report.package_id))
    pkg_obj = pkg_result.scalar_one_or_none()
    if reporter_user and pkg_obj:
        await send_report_resolved_reporter_email(
            reporter_user.email, pkg_obj.slug, body.status, body.resolution_note
        )

    return {"resolved": True, "status": body.status}


# --- GET /v1/admin/stats (Observability) ---

@router.get("/stats")
async def get_platform_stats(
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Platform-wide stats for the admin dashboard."""
    # User counts
    user_total = await session.execute(select(func.count(User.id)))
    total_users = user_total.scalar() or 0

    admin_count = await session.execute(
        select(func.count(User.id)).where(User.is_admin == True)  # noqa: E712
    )
    total_admins = admin_count.scalar() or 0

    verified_count = await session.execute(
        select(func.count(User.id)).where(User.is_email_verified == True)  # noqa: E712
    )
    total_verified = verified_count.scalar() or 0

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
        "users": {
            "total": total_users,
            "admins": total_admins,
            "email_verified": total_verified,
        },
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


# --- User Management ---


@router.get("/users")
async def list_users(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    search: str | None = None,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """List all users with optional search and pagination."""
    query = select(User).options(selectinload(User.publisher))
    count_query = select(func.count(User.id))

    if search:
        search_filter = or_(
            User.username.ilike(f"%{search}%"),
            User.email.ilike(f"%{search}%"),
        )
        query = query.where(search_filter)
        count_query = count_query.where(search_filter)

    total = (await session.execute(count_query)).scalar() or 0

    result = await session.execute(
        query.order_by(User.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    users = result.scalars().all()

    return {
        "users": [
            {
                "id": str(u.id),
                "email": u.email,
                "username": u.username,
                "is_admin": u.is_admin,
                "is_email_verified": u.is_email_verified,
                "two_factor_enabled": u.two_factor_enabled,
                "publisher_slug": u.publisher.slug if u.publisher else None,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            for u in users
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.post("/users/{user_id}/promote", dependencies=[Depends(rate_limit(5, 60))])
async def promote_user(
    user_id: str,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Promote a user to admin."""
    target = await session.execute(select(User).where(User.id == UUID(user_id)))
    target_user = target.scalar_one_or_none()
    if not target_user:
        raise AppError("USER_NOT_FOUND", "User not found", 404)
    if target_user.is_admin:
        raise AppError("ALREADY_ADMIN", "User is already an admin", 409)

    target_user.is_admin = True
    await _audit(session, request, user, "promote_admin", "user", user_id, {"username": target_user.username})
    await session.commit()
    return {"message": f"User '{target_user.username}' promoted to admin."}


@router.post("/users/{user_id}/demote", dependencies=[Depends(rate_limit(5, 60))])
async def demote_user(
    user_id: str,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Remove admin from a user."""
    if str(user.id) == user_id:
        raise AppError("SELF_DEMOTE", "You cannot demote yourself", 400)

    target = await session.execute(select(User).where(User.id == UUID(user_id)))
    target_user = target.scalar_one_or_none()
    if not target_user:
        raise AppError("USER_NOT_FOUND", "User not found", 404)
    if not target_user.is_admin:
        raise AppError("NOT_ADMIN", "User is not an admin", 409)

    target_user.is_admin = False
    await _audit(session, request, user, "demote_admin", "user", user_id, {"username": target_user.username})
    await session.commit()
    return {"message": f"User '{target_user.username}' demoted from admin."}


# --- All Publishers listing ---


@router.get("/publishers")
async def list_all_publishers(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    search: str | None = None,
    trust_level: str | None = None,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """List all publishers with search, filter, and pagination."""
    query = select(Publisher)
    count_query = select(func.count(Publisher.id))

    if search:
        search_filter = or_(
            Publisher.slug.ilike(f"%{search}%"),
            Publisher.display_name.ilike(f"%{search}%"),
        )
        query = query.where(search_filter)
        count_query = count_query.where(search_filter)

    if trust_level:
        query = query.where(Publisher.trust_level == trust_level)
        count_query = count_query.where(Publisher.trust_level == trust_level)

    total = (await session.execute(count_query)).scalar() or 0

    result = await session.execute(
        query.order_by(Publisher.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    publishers = result.scalars().all()

    return {
        "publishers": [
            {
                "id": str(p.id),
                "slug": p.slug,
                "display_name": p.display_name,
                "trust_level": p.trust_level,
                "is_suspended": p.is_suspended,
                "suspension_reason": p.suspension_reason,
                "packages_published_count": p.packages_published_count,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in publishers
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


# --- All Packages listing ---


@router.get("/packages")
async def list_all_packages(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    search: str | None = None,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """List all packages with search and pagination."""
    query = select(Package).options(selectinload(Package.publisher))
    count_query = select(func.count(Package.id))

    if search:
        search_filter = or_(
            Package.slug.ilike(f"%{search}%"),
            Package.name.ilike(f"%{search}%"),
        )
        query = query.where(search_filter)
        count_query = count_query.where(search_filter)

    total = (await session.execute(count_query)).scalar() or 0

    result = await session.execute(
        query.order_by(Package.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    packages = result.scalars().all()

    return {
        "packages": [
            {
                "id": str(p.id),
                "slug": p.slug,
                "name": p.name,
                "package_type": p.package_type,
                "publisher_slug": p.publisher.slug if p.publisher else None,
                "download_count": p.download_count,
                "is_deprecated": p.is_deprecated,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in packages
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


# --- Installations listing ---


# --- Audit Log ---


@router.get("/audit")
async def list_audit_logs(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    action: str | None = None,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """List admin audit logs with admin username, paginated."""
    admin_user = User.__table__.alias("admin_user")

    base_filters = []
    if action:
        base_filters.append(AdminAuditLog.action == action)

    count_query = select(func.count(AdminAuditLog.id))
    for f in base_filters:
        count_query = count_query.where(f)
    total = (await session.execute(count_query)).scalar() or 0

    query = (
        select(
            AdminAuditLog,
            admin_user.c.username.label("admin_username"),
            admin_user.c.email.label("admin_email"),
        )
        .outerjoin(admin_user, AdminAuditLog.admin_user_id == admin_user.c.id)
        .order_by(AdminAuditLog.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    for f in base_filters:
        query = query.where(f)

    result = await session.execute(query)
    rows = result.all()

    return {
        "logs": [
            {
                "id": str(r.AdminAuditLog.id),
                "admin_username": r.admin_username,
                "admin_email": r.admin_email,
                "action": r.AdminAuditLog.action,
                "target_type": r.AdminAuditLog.target_type,
                "target_id": r.AdminAuditLog.target_id,
                "metadata": r.AdminAuditLog.metadata_,
                "ip_address": r.AdminAuditLog.ip_address,
                "created_at": r.AdminAuditLog.created_at.isoformat() if r.AdminAuditLog.created_at else None,
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


# --- SMTP / Email Settings ---


class SmtpSettingsRequest(BaseModel):
    host: str = ""
    port: int = 587
    user: str = ""
    password: str = ""
    use_tls: bool = True
    from_email: str = "noreply@agentnode.net"
    from_name: str = "AgentNode"


@router.get("/settings/smtp")
async def get_smtp_settings(
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Get current SMTP configuration (password masked)."""
    result = await session.execute(
        select(SystemSetting).where(SystemSetting.key == "smtp")
    )
    row = result.scalar_one_or_none()

    if row and row.value:
        data = dict(row.value)
        # Mask password
        if data.get("password"):
            pw = data["password"]
            data["password_masked"] = pw[:2] + "*" * (len(pw) - 4) + pw[-2:] if len(pw) > 4 else "****"
            data["has_password"] = True
        else:
            data["password_masked"] = ""
            data["has_password"] = False
        data.pop("password", None)
        data["source"] = "database"
        data["updated_at"] = row.updated_at.isoformat() if row.updated_at else None
        return data

    # Fallback: show env var settings
    from app.config import settings as app_settings
    return {
        "host": app_settings.SMTP_HOST,
        "port": app_settings.SMTP_PORT,
        "user": app_settings.SMTP_USER,
        "password_masked": ("****" if app_settings.SMTP_PASSWORD else ""),
        "has_password": bool(app_settings.SMTP_PASSWORD),
        "use_tls": app_settings.SMTP_USE_TLS,
        "from_email": app_settings.EMAIL_FROM,
        "from_name": app_settings.EMAIL_FROM_NAME,
        "source": "environment",
        "updated_at": None,
    }


@router.put("/settings/smtp", dependencies=[Depends(rate_limit(5, 60))])
async def update_smtp_settings(
    body: SmtpSettingsRequest,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Update SMTP settings in the database."""
    result = await session.execute(
        select(SystemSetting).where(SystemSetting.key == "smtp")
    )
    row = result.scalar_one_or_none()

    value = {
        "host": body.host,
        "port": body.port,
        "user": body.user,
        "password": body.password,
        "use_tls": body.use_tls,
        "from_email": body.from_email,
        "from_name": body.from_name,
    }

    # If password is empty, keep the old password
    if not body.password and row and row.value:
        value["password"] = row.value.get("password", "")

    if row:
        row.value = value
        row.updated_at = datetime.now(timezone.utc)
    else:
        row = SystemSetting(key="smtp", value=value, updated_at=datetime.now(timezone.utc))
        session.add(row)

    await _audit(session, request, user, "update_smtp_settings", "system", "smtp")
    await session.commit()

    # Invalidate the email service cache
    from app.shared.email import invalidate_smtp_cache
    invalidate_smtp_cache()

    return {"message": "SMTP settings updated", "source": "database"}


@router.post("/settings/smtp/test", dependencies=[Depends(rate_limit(3, 60))])
async def test_smtp_settings(
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Send a test email using current SMTP settings."""
    from app.shared.email import send_email, invalidate_smtp_cache

    # Force fresh settings
    invalidate_smtp_cache()

    success = await send_email(
        to=user.email,
        subject="AgentNode SMTP Test",
        html_body=f"""<!DOCTYPE html><html><head>
        <style>
          body {{ font-family: -apple-system, sans-serif; background: #0a0a0a; color: #e5e5e5; margin: 0; padding: 0; }}
          .container {{ max-width: 520px; margin: 40px auto; padding: 32px; background: #141414; border: 1px solid #262626; border-radius: 12px; }}
          .logo {{ font-size: 20px; font-weight: 700; color: #ffffff; margin-bottom: 24px; }}
          .logo span {{ color: #6366f1; }}
          h1 {{ font-size: 18px; color: #ffffff; margin: 0 0 12px 0; }}
          p {{ font-size: 14px; line-height: 1.6; color: #a3a3a3; margin: 0 0 16px 0; }}
          .success {{ color: #22c55e; font-weight: 600; }}
        </style>
        </head><body>
        <div class="container">
          <div class="logo">Agent<span>Node</span></div>
          <h1>SMTP Test Successful</h1>
          <p class="success">Your email configuration is working correctly.</p>
          <p>This test email was triggered by <strong>{user.username}</strong> from the admin panel.</p>
          <p style="font-size:12px; color:#666;">Sent at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</p>
        </div>
        </body></html>""",
        text_body=f"AgentNode SMTP test successful. Triggered by {user.username}.",
    )

    await _audit(session, request, user, "test_smtp", "system", "smtp", {"success": success, "to": user.email})
    await session.commit()

    if success:
        return {"success": True, "message": f"Test email sent to {user.email}"}
    else:
        raise AppError("SMTP_TEST_FAILED", "Failed to send test email. Check your SMTP settings and server logs.", 400)


@router.get("/installations")
async def list_installations(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    status: str | None = None,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """List recent installations with readable package slug and username."""
    install_user = User.__table__.alias("install_user")

    base_filter = Installation.status == status if status else None

    count_query = select(func.count(Installation.id))
    if base_filter is not None:
        count_query = count_query.where(base_filter)
    total = (await session.execute(count_query)).scalar() or 0

    query = (
        select(
            Installation,
            Package.slug.label("package_slug"),
            Package.name.label("package_name"),
            install_user.c.username.label("username"),
        )
        .outerjoin(Package, Installation.package_id == Package.id)
        .outerjoin(install_user, Installation.user_id == install_user.c.id)
        .order_by(Installation.installed_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    if base_filter is not None:
        query = query.where(base_filter)

    result = await session.execute(query)
    rows = result.all()

    return {
        "installations": [
            {
                "id": str(r.Installation.id),
                "user_id": str(r.Installation.user_id) if r.Installation.user_id else None,
                "username": r.username,
                "package_id": str(r.Installation.package_id),
                "package_slug": r.package_slug,
                "package_name": r.package_name,
                "status": r.Installation.status,
                "source": r.Installation.source,
                "event_type": r.Installation.event_type,
                "installed_at": r.Installation.installed_at.isoformat() if r.Installation.installed_at else None,
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }
