import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.dependencies import get_current_user, require_publisher
from app.auth.models import User
from app.database import get_session
from app.shared.rate_limit import rate_limit
from app.packages.assembler import assemble_package_detail
from app.packages.models import Installation, Package, PackageReport, PackageVersion, Review
from app.packages.schemas import (
    PackageDetailResponse,
    PublishResponse,
    ValidateRequest,
    ValidateResponse,
    VersionListItem,
    VersionsResponse,
)
from app.packages.service import publish_package
from app.packages.validator import validate_manifest
from app.packages.version_queries import get_owner_visible_versions, get_public_versions
from app.shared.exceptions import AppError

router = APIRouter(prefix="/v1/packages", tags=["packages"])


@router.post("/validate", response_model=ValidateResponse)
async def validate_package(
    body: ValidateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    valid, errors, warnings = await validate_manifest(body.manifest, session)
    return ValidateResponse(valid=valid, errors=errors, warnings=warnings)


@router.post("/publish", response_model=PublishResponse, dependencies=[Depends(rate_limit(10, 60))])
async def publish(
    manifest: str = Form(...),
    artifact: UploadFile | None = File(None),
    user: User = Depends(require_publisher),
    session: AsyncSession = Depends(get_session),
):
    """Publish a new package or new version of an existing package."""
    manifest_dict = json.loads(manifest)

    artifact_bytes = None
    if artifact:
        artifact_bytes = await artifact.read()

    pkg, pv, warnings = await publish_package(
        manifest=manifest_dict,
        publisher_id=user.publisher.id,
        session=session,
        artifact_bytes=artifact_bytes,
    )

    message = f"Published {pkg.slug}@{pv.version_number}"
    if warnings:
        message += " (with warnings: " + "; ".join(warnings) + ")"

    return PublishResponse(
        slug=pkg.slug,
        version=pv.version_number,
        package_type=pkg.package_type,
        message=message,
    )


@router.get("/{slug}", response_model=PackageDetailResponse)
async def get_package(slug: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(Package)
        .options(
            selectinload(Package.publisher),
            selectinload(Package.latest_version)
            .selectinload(PackageVersion.capabilities),
            selectinload(Package.latest_version)
            .selectinload(PackageVersion.compatibility_rules),
            selectinload(Package.latest_version)
            .selectinload(PackageVersion.dependencies),
            selectinload(Package.latest_version)
            .selectinload(PackageVersion.permissions),
            selectinload(Package.latest_version)
            .selectinload(PackageVersion.upgrade_metadata),
            selectinload(Package.latest_version)
            .selectinload(PackageVersion.security_findings),
        )
        .where(Package.slug == slug)
    )
    pkg = result.scalar_one_or_none()
    if not pkg:
        raise AppError("PACKAGE_NOT_FOUND", f"Package '{slug}' not found", 404)

    return assemble_package_detail(pkg, pkg.latest_version)


@router.get("/{slug}/versions", response_model=VersionsResponse)
async def get_versions(
    slug: str,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Package).options(selectinload(Package.publisher)).where(Package.slug == slug)
    )
    pkg = result.scalar_one_or_none()
    if not pkg:
        raise AppError("PACKAGE_NOT_FOUND", f"Package '{slug}' not found", 404)

    versions = await get_public_versions(session, pkg.id)

    return VersionsResponse(
        versions=[
            VersionListItem(
                version_number=v.version_number,
                channel=v.channel,
                changelog=v.changelog,
                published_at=v.published_at,
            )
            for v in versions
        ]
    )


@router.get("/{slug}/versions/all", response_model=VersionsResponse)
async def get_all_versions(
    slug: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Owner-only: returns ALL versions including yanked/quarantined."""
    result = await session.execute(
        select(Package).options(selectinload(Package.publisher)).where(Package.slug == slug)
    )
    pkg = result.scalar_one_or_none()
    if not pkg:
        raise AppError("PACKAGE_NOT_FOUND", f"Package '{slug}' not found", 404)
    if pkg.publisher.user_id != user.id:
        raise AppError("PACKAGE_NOT_OWNED", "You do not own this package", 403)

    versions = await get_owner_visible_versions(session, pkg.id)

    return VersionsResponse(
        versions=[
            VersionListItem(
                version_number=v.version_number,
                channel=v.channel,
                changelog=v.changelog,
                published_at=v.published_at,
                quarantine_status=v.quarantine_status,
                is_yanked=v.is_yanked,
            )
            for v in versions
        ]
    )


@router.post("/{slug}/deprecate")
async def deprecate_package(
    slug: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Package).options(selectinload(Package.publisher)).where(Package.slug == slug)
    )
    pkg = result.scalar_one_or_none()
    if not pkg:
        raise AppError("PACKAGE_NOT_FOUND", f"Package '{slug}' not found", 404)
    if pkg.publisher.user_id != user.id:
        raise AppError("PACKAGE_NOT_OWNED", "You do not own this package", 403)

    pkg.is_deprecated = True

    from app.packages.version_queries import recalculate_latest_version_id
    await recalculate_latest_version_id(session, pkg.id)
    await session.commit()

    return {"deprecated": True}


@router.post("/{slug}/versions/{version}/yank")
async def yank_version(
    slug: str,
    version: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Package).options(selectinload(Package.publisher)).where(Package.slug == slug)
    )
    pkg = result.scalar_one_or_none()
    if not pkg:
        raise AppError("PACKAGE_NOT_FOUND", f"Package '{slug}' not found", 404)
    if pkg.publisher.user_id != user.id:
        raise AppError("PACKAGE_NOT_OWNED", "You do not own this package", 403)

    ver_result = await session.execute(
        select(PackageVersion).where(
            PackageVersion.package_id == pkg.id,
            PackageVersion.version_number == version,
        )
    )
    pv = ver_result.scalar_one_or_none()
    if not pv:
        raise AppError("PACKAGE_VERSION_NOT_FOUND", f"Version '{version}' not found", 404)

    pv.is_yanked = True

    from app.packages.version_queries import recalculate_latest_version_id
    await recalculate_latest_version_id(session, pkg.id)
    await session.commit()

    return {"yanked": True}


# --- Reviews (Spec §8.8) ---

class CreateReviewRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    comment: str | None = Field(None, max_length=1000)


@router.post("/{slug}/reviews", status_code=201)
async def create_review(
    slug: str,
    body: CreateReviewRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Submit a review. User must have at least one installation. Spec §8.8."""
    result = await session.execute(select(Package).where(Package.slug == slug))
    pkg = result.scalar_one_or_none()
    if not pkg:
        raise AppError("PACKAGE_NOT_FOUND", f"Package '{slug}' not found", 404)

    # Check user has installed this package
    inst_result = await session.execute(
        select(Installation.id).where(
            Installation.user_id == user.id,
            Installation.package_id == pkg.id,
        ).limit(1)
    )
    if not inst_result.scalar_one_or_none():
        raise AppError("REVIEW_NOT_ALLOWED", "You must install this package before reviewing", 403)

    # Check for existing review (unique constraint)
    existing = await session.execute(
        select(Review).where(
            Review.user_id == user.id,
            Review.package_id == pkg.id,
        )
    )
    if existing.scalar_one_or_none():
        raise AppError("REVIEW_EXISTS", "You have already reviewed this package", 409)

    review = Review(
        user_id=user.id,
        package_id=pkg.id,
        rating=body.rating,
        comment=body.comment,
    )
    session.add(review)
    await session.commit()

    return {"id": str(review.id)}


@router.get("/{slug}/reviews")
async def list_reviews(
    slug: str,
    session: AsyncSession = Depends(get_session),
):
    """List reviews for a package. Spec §8.8."""
    result = await session.execute(select(Package).where(Package.slug == slug))
    pkg = result.scalar_one_or_none()
    if not pkg:
        raise AppError("PACKAGE_NOT_FOUND", f"Package '{slug}' not found", 404)

    reviews_result = await session.execute(
        select(Review, User.username)
        .join(User, Review.user_id == User.id)
        .where(Review.package_id == pkg.id)
        .order_by(Review.created_at.desc())
    )
    rows = reviews_result.all()

    avg_result = await session.execute(
        select(func.avg(Review.rating)).where(Review.package_id == pkg.id)
    )
    avg_rating = avg_result.scalar() or 0

    reviews = [
        {
            "username": row.username,
            "rating": row.Review.rating,
            "comment": row.Review.comment,
            "created_at": row.Review.created_at.isoformat() if row.Review.created_at else None,
        }
        for row in rows
    ]

    return {"reviews": reviews, "avg_rating": round(float(avg_rating), 2), "total": len(reviews)}


# --- Reports (Spec §8.9) ---

VALID_REPORT_REASONS = {"malware", "typosquatting", "spam", "misleading", "policy_violation", "other"}


class CreateReportRequest(BaseModel):
    reason: str
    description: str


@router.post("/{slug}/report", status_code=201, dependencies=[Depends(rate_limit(10, 3600))])
async def create_report(
    slug: str,
    body: CreateReportRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Report a package. Spec §8.9."""
    if body.reason not in VALID_REPORT_REASONS:
        raise AppError("INVALID_REASON", f"Reason must be one of: {', '.join(sorted(VALID_REPORT_REASONS))}", 400)

    result = await session.execute(select(Package).where(Package.slug == slug))
    pkg = result.scalar_one_or_none()
    if not pkg:
        raise AppError("PACKAGE_NOT_FOUND", f"Package '{slug}' not found", 404)

    # Check max 3 active reports per user per package
    active_count_result = await session.execute(
        select(func.count()).select_from(PackageReport).where(
            PackageReport.reporter_user_id == user.id,
            PackageReport.package_id == pkg.id,
            PackageReport.status.in_(["submitted", "reviewing"]),
        )
    )
    active_count = active_count_result.scalar() or 0
    if active_count >= 3:
        raise AppError("RATE_LIMITED", "Max 3 active reports per package", 429)

    # Check max 10 reports per user per hour
    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    hourly_result = await session.execute(
        select(func.count()).select_from(PackageReport).where(
            PackageReport.reporter_user_id == user.id,
            PackageReport.created_at >= one_hour_ago,
        )
    )
    hourly_count = hourly_result.scalar() or 0
    if hourly_count >= 10:
        raise AppError("RATE_LIMITED", "Max 10 reports per hour", 429)

    report = PackageReport(
        package_id=pkg.id,
        reporter_user_id=user.id,
        reason=body.reason,
        description=body.description,
    )
    session.add(report)
    await session.commit()

    return {"report_id": str(report.id), "status": "submitted"}
