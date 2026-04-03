from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from pydantic import BaseModel, EmailStr, Field, field_validator
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
    background_tasks: BackgroundTasks,
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

    background_tasks.add_task(fire_event, session, pkg.publisher_id, "version.quarantined", {"slug": slug, "version": version, "reason": body.reason})

    from app.shared.email import send_quarantine_email, get_publisher_email
    pub_email = await get_publisher_email(pkg.publisher_id)
    if pub_email:
        background_tasks.add_task(send_quarantine_email, pub_email, slug, version, body.reason)

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
    background_tasks: BackgroundTasks,
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

    # Increment publisher's cleared count so future publishes skip quarantine
    from app.publishers.models import Publisher
    pub_result = await session.execute(
        select(Publisher).where(Publisher.id == pkg.publisher_id)
    )
    publisher_obj = pub_result.scalar_one_or_none()
    if publisher_obj:
        publisher_obj.packages_cleared_count = (publisher_obj.packages_cleared_count or 0) + 1

    # Sync to Meilisearch now that version is public
    from app.packages.service import build_meili_document
    from app.shared.meili import sync_package_to_meilisearch
    await session.refresh(pkg, ["publisher"])
    await sync_package_to_meilisearch(build_meili_document(pkg, pv, pv.manifest_raw or {}))

    await _audit(session, request, user, "clear_quarantine", "package", slug, {"version": version})
    await session.commit()

    background_tasks.add_task(fire_event, session, pkg.publisher_id, "version.cleared", {"slug": slug, "version": version})

    from app.shared.email import send_quarantine_cleared_email, get_publisher_email
    pub_email = await get_publisher_email(pkg.publisher_id)
    if pub_email:
        background_tasks.add_task(send_quarantine_cleared_email, pub_email, slug, version)

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
    background_tasks: BackgroundTasks,
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

    background_tasks.add_task(fire_event, session, pkg.publisher_id, "version.rejected", {"slug": slug, "version": version})

    from app.shared.email import send_version_rejected_email, get_publisher_email
    pub_email = await get_publisher_email(pkg.publisher_id)
    if pub_email:
        background_tasks.add_task(send_version_rejected_email, pub_email, slug, version)

    return QuarantineActionResponse(
        slug=slug, version=version,
        quarantine_status="rejected",
        message=f"Version {slug}@{version} rejected",
    )


@router.get("/quarantined", dependencies=[Depends(rate_limit(30, 60))])
async def list_quarantined(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """List all quarantined versions."""
    total_result = await session.execute(
        select(func.count(PackageVersion.id)).where(PackageVersion.quarantine_status == "quarantined")
    )
    total = total_result.scalar() or 0

    result = await session.execute(
        select(PackageVersion)
        .options(selectinload(PackageVersion.package))
        .where(PackageVersion.quarantine_status == "quarantined")
        .order_by(PackageVersion.quarantined_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    versions = result.scalars().all()

    return {
        "items": [
            QuarantinedVersionItem(
                package_slug=v.package.slug,
                version_number=v.version_number,
                quarantine_status=v.quarantine_status,
                quarantined_at=v.quarantined_at,
                quarantine_reason=v.quarantine_reason,
            ).model_dump()
            for v in versions
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


# --- Trust level endpoints ---


@router.put("/publishers/{slug}/trust", response_model=TrustLevelResponse, dependencies=[Depends(rate_limit(10, 60))])
async def set_trust_level(
    slug: str,
    body: SetTrustLevelRequest,
    request: Request,
    background_tasks: BackgroundTasks,
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
        background_tasks.add_task(send_trust_level_changed_email, pub_email, slug, old_level, body.trust_level)

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
    background_tasks: BackgroundTasks,
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
        background_tasks.add_task(send_publisher_suspended_email, pub_email, slug, body.reason)

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
    background_tasks: BackgroundTasks,
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
        background_tasks.add_task(send_publisher_unsuspended_email, pub_email, slug)

    return SuspensionResponse(
        publisher_slug=slug,
        is_suspended=False,
        suspension_reason=None,
        message=f"Publisher '{slug}' has been unsuspended",
    )


@router.get("/publishers/suspended", response_model=list[SuspendedPublisherItem], dependencies=[Depends(rate_limit(30, 60))])
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

    if version == "latest":
        if not pkg.latest_version_id:
            raise AppError("VERSION_NOT_FOUND", "No latest version found", 404)
        ver_result = await session.execute(
            select(PackageVersion).where(PackageVersion.id == pkg.latest_version_id)
        )
    else:
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


@router.get("/capabilities", dependencies=[Depends(rate_limit(30, 60))])
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


@router.post("/capabilities", status_code=201, dependencies=[Depends(rate_limit(10, 60))])
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


@router.put("/capabilities/{cap_id}", dependencies=[Depends(rate_limit(10, 60))])
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


@router.delete("/capabilities/{cap_id}", dependencies=[Depends(rate_limit(10, 60))])
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


@router.get("/reports", dependencies=[Depends(rate_limit(30, 60))])
async def list_reports(
    status: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """List package reports with readable package slug and reporter username."""
    # Aliases for the reporter user
    reporter = User.__table__.alias("reporter")

    count_query = select(func.count(PackageReport.id))
    if status:
        count_query = count_query.where(PackageReport.status == status)
    total_result = await session.execute(count_query)
    total = total_result.scalar() or 0

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
        .offset((page - 1) * per_page)
        .limit(per_page)
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
        "total": total,
        "page": page,
        "per_page": per_page,
    }


class ResolveReportRequest(BaseModel):
    status: str = Field(..., pattern=r"^(resolved|dismissed)$")
    resolution_note: str | None = None


@router.post("/reports/{report_id}/resolve", dependencies=[Depends(rate_limit(10, 60))])
async def resolve_report(
    report_id: str,
    body: ResolveReportRequest,
    request: Request,
    background_tasks: BackgroundTasks,
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
        background_tasks.add_task(
            send_report_resolved_reporter_email,
            reporter_user.email, pkg_obj.slug, body.status, body.resolution_note,
        )

    return {"resolved": True, "status": body.status}


# --- Report Admin Actions ---


@router.delete("/reports/{report_id}", dependencies=[Depends(rate_limit(10, 60))])
async def delete_report(
    report_id: str,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Delete a report."""
    result = await session.execute(select(PackageReport).where(PackageReport.id == UUID(report_id)))
    report = result.scalar_one_or_none()
    if not report:
        raise AppError("REPORT_NOT_FOUND", "Report not found", 404)
    await _audit(session, request, user, "delete_report", "report", report_id)
    await session.delete(report)
    await session.commit()
    return {"message": "Report deleted."}


@router.post("/reports/{report_id}/reopen", dependencies=[Depends(rate_limit(10, 60))])
async def reopen_report(
    report_id: str,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Reopen a resolved or dismissed report."""
    result = await session.execute(select(PackageReport).where(PackageReport.id == UUID(report_id)))
    report = result.scalar_one_or_none()
    if not report:
        raise AppError("REPORT_NOT_FOUND", "Report not found", 404)
    if report.status == "submitted":
        raise AppError("ALREADY_OPEN", "Report is already open", 409)
    report.status = "submitted"
    report.resolution_note = None
    report.resolved_by = None
    report.resolved_at = None
    await _audit(session, request, user, "reopen_report", "report", report_id)
    await session.commit()
    return {"message": "Report reopened."}


# --- GET /v1/admin/stats (Observability) ---

@router.get("/stats", dependencies=[Depends(rate_limit(30, 60))])
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


@router.get("/users", dependencies=[Depends(rate_limit(30, 60))])
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
        safe_search = search.replace("%", r"\%").replace("_", r"\_")
        search_filter = or_(
            User.username.ilike(f"%{safe_search}%", escape="\\"),
            User.email.ilike(f"%{safe_search}%", escape="\\"),
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
                "is_banned": u.is_banned,
                "ban_reason": u.ban_reason,
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


# --- User Ban/Suspend ---

class BanUserRequest(BaseModel):
    reason: str = Field("Admin action", max_length=500)

class EditUserRequest(BaseModel):
    email: EmailStr | None = None
    username: str | None = None

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str | None) -> str | None:
        if v is None:
            return v
        import re
        v = v.lower()
        if not re.match(r"^[a-z0-9_-]{3,30}$", v):
            raise ValueError("Username must be 3-30 chars, only lowercase letters, digits, hyphens, underscores")
        return v


@router.post("/users/{user_id}/ban", dependencies=[Depends(rate_limit(5, 60))])
async def ban_user(
    user_id: str,
    body: BanUserRequest,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Ban a user."""
    if str(user.id) == user_id:
        raise AppError("SELF_BAN", "You cannot ban yourself", 400)
    target = await session.execute(select(User).where(User.id == UUID(user_id)))
    target_user = target.scalar_one_or_none()
    if not target_user:
        raise AppError("USER_NOT_FOUND", "User not found", 404)
    if target_user.is_banned:
        raise AppError("ALREADY_BANNED", "User is already banned", 409)
    target_user.is_banned = True
    target_user.ban_reason = body.reason
    await _audit(session, request, user, "ban_user", "user", user_id, {"username": target_user.username, "reason": body.reason})
    await session.commit()
    return {"message": f"User '{target_user.username}' has been banned."}


@router.post("/users/{user_id}/unban", dependencies=[Depends(rate_limit(5, 60))])
async def unban_user(
    user_id: str,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Unban a user."""
    target = await session.execute(select(User).where(User.id == UUID(user_id)))
    target_user = target.scalar_one_or_none()
    if not target_user:
        raise AppError("USER_NOT_FOUND", "User not found", 404)
    if not target_user.is_banned:
        raise AppError("NOT_BANNED", "User is not banned", 409)
    target_user.is_banned = False
    target_user.ban_reason = None
    await _audit(session, request, user, "unban_user", "user", user_id, {"username": target_user.username})
    await session.commit()
    return {"message": f"User '{target_user.username}' has been unbanned."}


@router.post("/users/{user_id}/verify-email", dependencies=[Depends(rate_limit(5, 60))])
async def verify_user_email(
    user_id: str,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Manually verify a user's email."""
    target = await session.execute(select(User).where(User.id == UUID(user_id)))
    target_user = target.scalar_one_or_none()
    if not target_user:
        raise AppError("USER_NOT_FOUND", "User not found", 404)
    if target_user.is_email_verified:
        raise AppError("ALREADY_VERIFIED", "Email is already verified", 409)
    target_user.is_email_verified = True
    await _audit(session, request, user, "verify_email", "user", user_id, {"username": target_user.username})
    await session.commit()
    return {"message": f"Email verified for '{target_user.username}'."}


@router.post("/users/{user_id}/unverify-email", dependencies=[Depends(rate_limit(5, 60))])
async def unverify_user_email(
    user_id: str,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Manually unverify a user's email."""
    target = await session.execute(select(User).where(User.id == UUID(user_id)))
    target_user = target.scalar_one_or_none()
    if not target_user:
        raise AppError("USER_NOT_FOUND", "User not found", 404)
    if not target_user.is_email_verified:
        raise AppError("NOT_VERIFIED", "Email is not verified", 409)
    target_user.is_email_verified = False
    await _audit(session, request, user, "unverify_email", "user", user_id, {"username": target_user.username})
    await session.commit()
    return {"message": f"Email unverified for '{target_user.username}'."}


@router.post("/users/{user_id}/disable-2fa", dependencies=[Depends(rate_limit(5, 60))])
async def disable_user_2fa(
    user_id: str,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Disable 2FA for a user (admin override for lockout recovery)."""
    target = await session.execute(select(User).where(User.id == UUID(user_id)))
    target_user = target.scalar_one_or_none()
    if not target_user:
        raise AppError("USER_NOT_FOUND", "User not found", 404)
    if not target_user.two_factor_enabled:
        raise AppError("2FA_NOT_ENABLED", "2FA is not enabled for this user", 409)
    target_user.two_factor_enabled = False
    target_user.two_factor_secret = None
    await _audit(session, request, user, "disable_2fa", "user", user_id, {"username": target_user.username})
    await session.commit()
    return {"message": f"2FA disabled for '{target_user.username}'."}


@router.post("/users/{user_id}/reset-password", dependencies=[Depends(rate_limit(3, 60))])
async def reset_user_password(
    user_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Reset a user's password to a temporary one and optionally send email."""
    import secrets
    from app.auth.security import hash_password

    target = await session.execute(select(User).where(User.id == UUID(user_id)))
    target_user = target.scalar_one_or_none()
    if not target_user:
        raise AppError("USER_NOT_FOUND", "User not found", 404)

    temp_password = f"Tmp-{secrets.token_urlsafe(12)}"
    target_user.password_hash = hash_password(temp_password)
    await _audit(session, request, user, "reset_password", "user", user_id, {"username": target_user.username})
    await session.commit()

    # Send email in background
    from app.shared.email import send_email
    background_tasks.add_task(
        send_email,
        to=target_user.email,
        subject="AgentNode — Password Reset by Admin",
        html_body=f"<p>Your password has been reset by an administrator.</p><p>Temporary password: <strong>{temp_password}</strong></p><p>Please change it immediately after logging in.</p>",
        text_body=f"Your password has been reset by an administrator. Temporary password: {temp_password}",
    )

    return {"message": f"Password reset for '{target_user.username}'. Temporary password sent via email."}


@router.put("/users/{user_id}", dependencies=[Depends(rate_limit(5, 60))])
async def edit_user(
    user_id: str,
    body: EditUserRequest,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Edit a user's profile (email, username)."""
    target = await session.execute(select(User).where(User.id == UUID(user_id)))
    target_user = target.scalar_one_or_none()
    if not target_user:
        raise AppError("USER_NOT_FOUND", "User not found", 404)

    changes = {}
    if body.email and body.email != target_user.email:
        # Check uniqueness
        existing = await session.execute(select(User).where(User.email == body.email))
        if existing.scalar_one_or_none():
            raise AppError("EMAIL_TAKEN", "Email is already in use", 409)
        changes["email"] = {"old": target_user.email, "new": body.email}
        target_user.email = body.email

    if body.username and body.username != target_user.username:
        existing = await session.execute(select(User).where(User.username == body.username))
        if existing.scalar_one_or_none():
            raise AppError("USERNAME_TAKEN", "Username is already in use", 409)
        changes["username"] = {"old": target_user.username, "new": body.username}
        target_user.username = body.username

    if not changes:
        return {"message": "No changes made."}

    await _audit(session, request, user, "edit_user", "user", user_id, changes)
    await session.commit()
    return {"message": f"User updated.", "changes": changes}


@router.delete("/users/{user_id}", dependencies=[Depends(rate_limit(3, 60))])
async def delete_user(
    user_id: str,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Delete a user account permanently."""
    if str(user.id) == user_id:
        raise AppError("SELF_DELETE", "You cannot delete yourself", 400)

    target = await session.execute(select(User).where(User.id == UUID(user_id)))
    target_user = target.scalar_one_or_none()
    if not target_user:
        raise AppError("USER_NOT_FOUND", "User not found", 404)

    username = target_user.username
    await _audit(session, request, user, "delete_user", "user", user_id, {"username": username, "email": target_user.email})
    await session.delete(target_user)
    await session.commit()
    return {"message": f"User '{username}' deleted permanently."}


# --- All Publishers listing ---


@router.get("/publishers", dependencies=[Depends(rate_limit(30, 60))])
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
        safe_search = search.replace("%", r"\%").replace("_", r"\_")
        search_filter = or_(
            Publisher.slug.ilike(f"%{safe_search}%", escape="\\"),
            Publisher.display_name.ilike(f"%{safe_search}%", escape="\\"),
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


# --- Publisher Admin Actions ---


@router.delete("/publishers/{slug}", dependencies=[Depends(rate_limit(3, 60))])
async def delete_publisher(
    slug: str,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Delete a publisher and all associated packages permanently."""
    pub = await _get_publisher(session, slug)
    await _audit(session, request, user, "delete_publisher", "publisher", slug, {"display_name": pub.display_name})
    await session.delete(pub)
    await session.commit()
    return {"message": f"Publisher '{slug}' deleted permanently."}


# --- All Packages listing ---


@router.get("/packages", dependencies=[Depends(rate_limit(30, 60))])
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
        safe_search = search.replace("%", r"\%").replace("_", r"\_")
        search_filter = or_(
            Package.slug.ilike(f"%{safe_search}%", escape="\\"),
            Package.name.ilike(f"%{safe_search}%", escape="\\"),
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
                "summary": p.summary,
                "description": p.description,
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


# --- Package Admin Actions ---


@router.get("/packages/{slug}/versions", dependencies=[Depends(rate_limit(30, 60))])
async def list_package_versions(
    slug: str,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Admin: list all versions of a package including yanked/quarantined."""
    result = await session.execute(select(Package).where(Package.slug == slug))
    pkg = result.scalar_one_or_none()
    if not pkg:
        raise AppError("PACKAGE_NOT_FOUND", f"Package '{slug}' not found", 404)

    from app.packages.version_queries import get_owner_visible_versions
    versions = await get_owner_visible_versions(session, pkg.id)

    return {
        "versions": [
            {
                "version_number": v.version_number,
                "channel": v.channel,
                "changelog": v.changelog,
                "published_at": v.published_at.isoformat() if v.published_at else None,
                "quarantine_status": v.quarantine_status,
                "is_yanked": v.is_yanked,
                "verification_status": v.verification_status,
            }
            for v in versions
        ]
    }


class EditPackageRequest(BaseModel):
    name: str | None = None
    summary: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    homepage_url: str | None = None
    docs_url: str | None = None
    source_url: str | None = None


@router.post("/packages/{slug}/deprecate", dependencies=[Depends(rate_limit(10, 60))])
async def deprecate_package(
    slug: str,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Mark a package as deprecated."""
    result = await session.execute(select(Package).where(Package.slug == slug))
    pkg = result.scalar_one_or_none()
    if not pkg:
        raise AppError("PACKAGE_NOT_FOUND", f"Package '{slug}' not found", 404)
    if pkg.is_deprecated:
        raise AppError("ALREADY_DEPRECATED", "Package is already deprecated", 409)
    pkg.is_deprecated = True
    await _audit(session, request, user, "deprecate_package", "package", slug)
    await session.commit()
    return {"message": f"Package '{slug}' deprecated."}


@router.post("/packages/{slug}/undeprecate", dependencies=[Depends(rate_limit(10, 60))])
async def undeprecate_package(
    slug: str,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Remove deprecation from a package."""
    result = await session.execute(select(Package).where(Package.slug == slug))
    pkg = result.scalar_one_or_none()
    if not pkg:
        raise AppError("PACKAGE_NOT_FOUND", f"Package '{slug}' not found", 404)
    if not pkg.is_deprecated:
        raise AppError("NOT_DEPRECATED", "Package is not deprecated", 409)
    pkg.is_deprecated = False
    await _audit(session, request, user, "undeprecate_package", "package", slug)
    await session.commit()
    return {"message": f"Package '{slug}' undeprecated."}


@router.put("/packages/{slug}", dependencies=[Depends(rate_limit(10, 60))])
async def edit_package(
    slug: str,
    body: EditPackageRequest,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Edit package metadata. Admins can edit all fields that owners can."""
    result = await session.execute(select(Package).where(Package.slug == slug))
    pkg = result.scalar_one_or_none()
    if not pkg:
        raise AppError("PACKAGE_NOT_FOUND", f"Package '{slug}' not found", 404)

    changes = {}

    # Package-level fields
    if body.name is not None and body.name != pkg.name:
        changes["name"] = {"old": pkg.name, "new": body.name}
        pkg.name = body.name
    if body.summary is not None and body.summary != pkg.summary:
        changes["summary"] = {"old": pkg.summary, "new": body.summary}
        pkg.summary = body.summary
    if body.description is not None and body.description != pkg.description:
        changes["description"] = {"old": pkg.description, "new": body.description}
        pkg.description = body.description or None

    # Version-level fields — apply to latest non-yanked version
    version_fields = {k: v for k, v in {
        "tags": body.tags,
        "homepage_url": body.homepage_url,
        "docs_url": body.docs_url,
        "source_url": body.source_url,
    }.items() if v is not None}

    if version_fields:
        from app.packages.version_queries import get_latest_owner_visible_version
        pv = await get_latest_owner_visible_version(session, pkg.id)
        if not pv:
            raise AppError("NO_EDITABLE_VERSION", "No editable version found", 409)

        if "tags" in version_fields:
            from app.packages.models import PackageTag
            from sqlalchemy import delete
            old_tags_result = await session.execute(
                select(PackageTag.tag).where(PackageTag.package_version_id == pv.id)
            )
            old_tags = [r[0] for r in old_tags_result.all()]
            normalized_tags = list(dict.fromkeys(
                t.strip().lower() for t in version_fields["tags"] if t.strip()
            ))
            changes["tags"] = {"old": old_tags, "new": normalized_tags}
            await session.execute(
                delete(PackageTag).where(PackageTag.package_version_id == pv.id)
            )
            for tag in normalized_tags:
                session.add(PackageTag(package_version_id=pv.id, tag=tag))

        if "homepage_url" in version_fields:
            changes["homepage_url"] = {"old": pv.homepage_url, "new": version_fields["homepage_url"]}
            pv.homepage_url = version_fields["homepage_url"] or None
        if "docs_url" in version_fields:
            changes["docs_url"] = {"old": pv.docs_url, "new": version_fields["docs_url"]}
            pv.docs_url = version_fields["docs_url"] or None
        if "source_url" in version_fields:
            changes["source_url"] = {"old": pv.source_url, "new": version_fields["source_url"]}
            pv.source_url = version_fields["source_url"] or None

    if not changes:
        return {"message": "No changes made."}

    await _audit(session, request, user, "edit_package", "package", slug, changes)
    await session.commit()
    return {"message": f"Package '{slug}' updated.", "changes": changes}


@router.delete("/packages/{slug}/versions/{version}", dependencies=[Depends(rate_limit(5, 60))])
async def delete_version(
    slug: str,
    version: str,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Delete a specific package version."""
    pkg, pv = await _get_package_version(session, slug, version)
    await _audit(session, request, user, "delete_version", "package", slug, {"version": version})
    await session.delete(pv)
    await recalculate_latest_version_id(session, pkg.id)
    await session.commit()
    return {"message": f"Version {slug}@{version} deleted."}


@router.delete("/packages/{slug}", dependencies=[Depends(rate_limit(3, 60))])
async def delete_package(
    slug: str,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Delete a package and all its versions permanently."""
    result = await session.execute(select(Package).where(Package.slug == slug))
    pkg = result.scalar_one_or_none()
    if not pkg:
        raise AppError("PACKAGE_NOT_FOUND", f"Package '{slug}' not found", 404)

    await _audit(session, request, user, "delete_package", "package", slug, {"name": pkg.name})
    await session.delete(pkg)
    await session.commit()
    return {"message": f"Package '{slug}' deleted permanently."}


@router.post("/packages/{slug}/versions/{version}/yank", dependencies=[Depends(rate_limit(10, 60))])
async def yank_version(
    slug: str,
    version: str,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Yank a specific version. Same as owner yank but without ownership check."""
    pkg, pv = await _get_package_version(session, slug, version)
    if pv.is_yanked:
        raise AppError("ALREADY_YANKED", f"Version '{version}' is already yanked", 409)
    pv.is_yanked = True
    await recalculate_latest_version_id(session, pkg.id)
    await _audit(session, request, user, "yank_version", "package", slug, {"version": version})
    await session.commit()
    return {"message": f"Version {slug}@{version} yanked."}


@router.post("/packages/{slug}/versions/{version}/unyank", dependencies=[Depends(rate_limit(10, 60))])
async def unyank_version(
    slug: str,
    version: str,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Restore a yanked version. Admin-only — owners cannot unyank."""
    pkg, pv = await _get_package_version(session, slug, version)
    if not pv.is_yanked:
        raise AppError("NOT_YANKED", f"Version '{version}' is not yanked", 409)
    pv.is_yanked = False
    await recalculate_latest_version_id(session, pkg.id)
    await _audit(session, request, user, "unyank_version", "package", slug, {"version": version})
    await session.commit()
    return {"message": f"Version {slug}@{version} unyanked."}


# --- Installations listing ---


# --- Audit Log ---


@router.get("/audit", dependencies=[Depends(rate_limit(30, 60))])
async def list_audit_logs(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    action: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    admin_username: str | None = None,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """List admin audit logs with filters and pagination."""
    admin_user = User.__table__.alias("admin_user")

    base_filters = []
    if action:
        base_filters.append(AdminAuditLog.action == action)
    if date_from:
        base_filters.append(AdminAuditLog.created_at >= date_from)
    if date_to:
        base_filters.append(AdminAuditLog.created_at <= date_to)
    if admin_username:
        safe_name = admin_username.replace("%", r"\%").replace("_", r"\_")
        base_filters.append(AdminAuditLog.admin_user_id.in_(select(User.id).where(User.username.ilike(f"%{safe_name}%", escape="\\"))))

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


@router.get("/audit/export", dependencies=[Depends(rate_limit(30, 60))])
async def export_audit_logs(
    action: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Export audit logs as JSON (up to 1000 entries)."""
    admin_user_alias = User.__table__.alias("admin_user")

    base_filters = []
    if action:
        base_filters.append(AdminAuditLog.action == action)
    if date_from:
        base_filters.append(AdminAuditLog.created_at >= date_from)
    if date_to:
        base_filters.append(AdminAuditLog.created_at <= date_to)

    query = (
        select(
            AdminAuditLog,
            admin_user_alias.c.username.label("admin_username"),
        )
        .outerjoin(admin_user_alias, AdminAuditLog.admin_user_id == admin_user_alias.c.id)
        .order_by(AdminAuditLog.created_at.desc())
        .limit(1000)
    )
    for f in base_filters:
        query = query.where(f)

    result = await session.execute(query)
    rows = result.all()

    return {
        "export": [
            {
                "timestamp": r.AdminAuditLog.created_at.isoformat() if r.AdminAuditLog.created_at else None,
                "admin": r.admin_username,
                "action": r.AdminAuditLog.action,
                "target_type": r.AdminAuditLog.target_type,
                "target_id": r.AdminAuditLog.target_id,
                "metadata": r.AdminAuditLog.metadata_,
                "ip_address": r.AdminAuditLog.ip_address,
            }
            for r in rows
        ],
        "total": len(rows),
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


@router.get("/settings/smtp", dependencies=[Depends(rate_limit(30, 60))])
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
    """Update SMTP settings in the database.

    Note: SMTP config is cached at startup. Changes saved here take effect
    after the service is restarted (or the cache is explicitly reloaded).
    """
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

    # Reload SMTP config into the in-memory cache so changes take effect immediately
    from app.shared.email import load_smtp_config
    await load_smtp_config(session)

    return {"message": "SMTP settings updated", "source": "database"}


@router.post("/settings/smtp/test", dependencies=[Depends(rate_limit(3, 60))])
async def test_smtp_settings(
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Send a test email using current SMTP settings."""
    from app.shared.email import send_email, load_smtp_config

    # Reload settings from DB so the test uses the latest saved config
    await load_smtp_config(session)

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


# --- API Keys Settings ---


class ApiKeysRequest(BaseModel):
    anthropic_api_key: str = ""


@router.get("/settings/api-keys", dependencies=[Depends(rate_limit(30, 60))])
async def get_api_keys(
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Get current API key configuration (keys masked)."""
    result = await session.execute(
        select(SystemSetting).where(SystemSetting.key == "api_keys")
    )
    row = result.scalar_one_or_none()

    def _mask(key: str) -> dict:
        if not key:
            return {"masked": "", "is_set": False}
        if len(key) > 8:
            return {"masked": key[:4] + "*" * (len(key) - 8) + key[-4:], "is_set": True}
        return {"masked": "****", "is_set": True}

    if row and row.value:
        data = dict(row.value)
        return {
            "anthropic_api_key": _mask(data.get("anthropic_api_key", "")),
            "source": "database",
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    # Fallback: show env var settings
    from app.config import settings as app_settings
    return {
        "anthropic_api_key": _mask(app_settings.ANTHROPIC_API_KEY),
        "source": "environment",
        "updated_at": None,
    }


@router.put("/settings/api-keys", dependencies=[Depends(rate_limit(5, 60))])
async def update_api_keys(
    body: ApiKeysRequest,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Update API keys in the database."""
    result = await session.execute(
        select(SystemSetting).where(SystemSetting.key == "api_keys")
    )
    row = result.scalar_one_or_none()

    value = {}

    # If key is empty, keep old value
    old_value = (row.value if row and row.value else {})

    value["anthropic_api_key"] = body.anthropic_api_key if body.anthropic_api_key else old_value.get("anthropic_api_key", "")

    if row:
        row.value = value
        row.updated_at = datetime.now(timezone.utc)
    else:
        row = SystemSetting(key="api_keys", value=value, updated_at=datetime.now(timezone.utc))
        session.add(row)

    await _audit(session, request, user, "update_api_keys", "system", "api_keys")
    await session.commit()

    # Reload settings into the running process
    from app.config import settings as app_settings
    if value.get("anthropic_api_key"):
        app_settings.ANTHROPIC_API_KEY = value["anthropic_api_key"]

    return {"message": "API keys updated", "source": "database"}


@router.get("/installations", dependencies=[Depends(rate_limit(30, 60))])
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


@router.delete("/installations/{installation_id}", dependencies=[Depends(rate_limit(10, 60))])
async def delete_installation(
    installation_id: str,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Delete an installation record."""
    result = await session.execute(select(Installation).where(Installation.id == UUID(installation_id)))
    inst = result.scalar_one_or_none()
    if not inst:
        raise AppError("INSTALLATION_NOT_FOUND", "Installation not found", 404)
    await _audit(session, request, user, "delete_installation", "installation", installation_id)
    await session.delete(inst)
    await session.commit()
    return {"message": "Installation deleted."}


# --- Verification re-trigger ---

@router.post("/packages/{slug}/versions/{version}/reverify", dependencies=[Depends(rate_limit(10, 60))])
async def reverify_version(
    slug: str,
    version: str,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Re-trigger verification pipeline for a specific version."""
    pkg, pv = await _get_package_version(session, slug, version)

    if not pv.artifact_object_key:
        raise AppError("NO_ARTIFACT", "Version has no artifact to verify", 400)

    await _audit(session, request, user, "reverify_version", "package", slug, {"version": version})
    await session.commit()

    from app.verification.pipeline import run_verification
    import asyncio
    asyncio.get_running_loop().create_task(run_verification(pv.id, triggered_by="admin_reverify"))

    return {"message": f"Verification re-triggered for {slug}@{version}"}


# --- Phase 3B: Verification stats + batch reverify + regressions ---

@router.get("/verification-stats", dependencies=[Depends(rate_limit(30, 60))])
async def verification_stats(
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Distribution by smoke_status, smoke_reason, overall_status. Latest result per package only."""
    from app.verification.models import VerificationResult

    # Only latest results per package (via latest_verification_result_id FK)
    latest_ids_q = (
        select(PackageVersion.latest_verification_result_id)
        .where(PackageVersion.latest_verification_result_id.isnot(None))
    )
    latest_results = await session.execute(
        select(VerificationResult).where(VerificationResult.id.in_(latest_ids_q))
    )
    rows = latest_results.scalars().all()

    by_smoke_status: dict[str, int] = {}
    by_smoke_reason: dict[str, int] = {}
    by_overall_status: dict[str, int] = {}
    total_verified = 0

    for vr in rows:
        total_verified += 1
        ss = vr.smoke_status or "null"
        by_smoke_status[ss] = by_smoke_status.get(ss, 0) + 1

        sr = vr.smoke_reason or "null"
        by_smoke_reason[sr] = by_smoke_reason.get(sr, 0) + 1

        os_ = vr.status or "null"
        by_overall_status[os_] = by_overall_status.get(os_, 0) + 1

    total_packages_result = await session.execute(select(func.count(Package.id)))
    total_packages = total_packages_result.scalar() or 0

    return {
        "by_smoke_status": by_smoke_status,
        "by_smoke_reason": by_smoke_reason,
        "by_overall_status": by_overall_status,
        "total_packages": total_packages,
        "total_verified": total_verified,
    }


class ReverifyBatchRequest(BaseModel):
    smoke_reason: str | None = None
    smoke_status: str | None = None
    slugs: list[str] | None = None
    limit: int = Field(10, ge=1, le=50)


@router.post("/packages/reverify-batch", dependencies=[Depends(rate_limit(5, 60))])
async def reverify_batch(
    body: ReverifyBatchRequest,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Targeted batch re-verification by reason, status, or slugs."""
    from app.verification.models import VerificationResult
    from app.verification.pipeline import run_verification
    import asyncio

    query = (
        select(PackageVersion)
        .join(Package, Package.id == PackageVersion.package_id)
        .where(
            PackageVersion.artifact_object_key.isnot(None),
            PackageVersion.verification_status != "running",
        )
    )

    if body.slugs:
        query = query.where(Package.slug.in_(body.slugs))

    if body.smoke_reason or body.smoke_status:
        query = query.where(PackageVersion.latest_verification_result_id.isnot(None))
        # Need subquery to filter by VR fields
        vr_filter = select(VerificationResult.id)
        if body.smoke_reason:
            vr_filter = vr_filter.where(VerificationResult.smoke_reason == body.smoke_reason)
        if body.smoke_status:
            vr_filter = vr_filter.where(VerificationResult.smoke_status == body.smoke_status)
        query = query.where(PackageVersion.latest_verification_result_id.in_(vr_filter))

    query = query.limit(body.limit)
    result = await session.execute(query.options(selectinload(PackageVersion.package)))
    versions = result.scalars().all()

    triggered = []
    for pv in versions:
        asyncio.get_running_loop().create_task(run_verification(pv.id, triggered_by="admin_reverify"))
        triggered.append({"slug": pv.package.slug, "version": pv.version_number})

    await _audit(session, request, user, "reverify_batch", "verification", "batch", {
        "count": len(triggered),
        "smoke_reason": body.smoke_reason,
        "smoke_status": body.smoke_status,
        "slugs": body.slugs,
    })
    await session.commit()

    return {"triggered": triggered, "count": len(triggered)}


@router.get("/verification-regressions", dependencies=[Depends(rate_limit(30, 60))])
async def verification_regressions(
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Packages where verification status changed between last two runs."""
    from app.verification.models import VerificationResult
    from sqlalchemy import text

    # Window function: last 2 results per package_version, compare statuses
    sql = text("""
        WITH ranked AS (
            SELECT
                vr.package_version_id,
                vr.status,
                vr.smoke_status,
                vr.smoke_reason,
                vr.completed_at,
                ROW_NUMBER() OVER (
                    PARTITION BY vr.package_version_id
                    ORDER BY vr.created_at DESC
                ) AS rn
            FROM verification_results vr
            WHERE vr.status IN ('passed', 'failed', 'error')
        )
        SELECT
            pv.id AS version_id,
            p.slug,
            pv.version_number,
            curr.status AS current_status,
            curr.smoke_status AS current_smoke_status,
            curr.smoke_reason AS current_smoke_reason,
            prev.status AS previous_status,
            prev.smoke_status AS previous_smoke_status,
            prev.smoke_reason AS previous_smoke_reason,
            curr.completed_at
        FROM ranked curr
        JOIN ranked prev ON curr.package_version_id = prev.package_version_id AND prev.rn = 2
        JOIN package_versions pv ON pv.id = curr.package_version_id
        JOIN packages p ON p.id = pv.package_id
        WHERE curr.rn = 1
          AND (curr.status != prev.status OR curr.smoke_status != prev.smoke_status)
        ORDER BY curr.completed_at DESC
        LIMIT 50
    """)

    result = await session.execute(sql)
    rows = result.mappings().all()

    return {
        "regressions": [
            {
                "slug": r["slug"],
                "version": r["version_number"],
                "current_status": r["current_status"],
                "current_smoke_status": r["current_smoke_status"],
                "current_smoke_reason": r["current_smoke_reason"],
                "previous_status": r["previous_status"],
                "previous_smoke_status": r["previous_smoke_status"],
                "previous_smoke_reason": r["previous_smoke_reason"],
                "completed_at": r["completed_at"].isoformat() if r["completed_at"] else None,
            }
            for r in rows
        ],
        "count": len(rows),
    }
