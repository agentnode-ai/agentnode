import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, UploadFile
from fastapi.responses import PlainTextResponse
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
    ActionResponse,
    PackageDetailResponse,
    PublishResponse,
    UpdatePackageRequest,
    ValidateRequest,
    ValidateResponse,
    VersionListItem,
    VersionsResponse,
)
from app.packages.service import publish_package
from app.packages.validator import validate_manifest
from app.packages.version_queries import get_latest_installable_version, get_latest_owner_visible_version, get_owner_visible_versions, get_public_versions
from app.config import settings
from app.shared.exceptions import AppError
from app.shared.storage import download_preview_file, PREVIEW_EXTENSIONS
from app.trust.scanner import run_security_scan
from app.webhooks.service import fire_event

router = APIRouter(prefix="/v1/packages", tags=["packages"])


@router.post("/validate", response_model=ValidateResponse, dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))])
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
    background_tasks: BackgroundTasks = BackgroundTasks(),
    user: User = Depends(require_publisher),
    session: AsyncSession = Depends(get_session),
):
    """Publish a new package or new version of an existing package."""
    if not manifest or not manifest.strip():
        raise AppError("MANIFEST_INVALID", "Manifest must not be empty", 400)
    try:
        manifest_dict = json.loads(manifest)
    except (json.JSONDecodeError, ValueError) as exc:
        raise AppError("MANIFEST_INVALID", f"Manifest is not valid JSON: {exc}", 400)

    artifact_bytes = None
    if artifact:
        limit = settings.MAX_ARTIFACT_SIZE_BYTES
        artifact_bytes = await artifact.read(limit + 1)
        if len(artifact_bytes) > limit:
            limit_mb = limit / (1024 * 1024)
            raise AppError("ARTIFACT_TOO_LARGE", f"Artifact must be under {limit_mb:.0f} MB", 413)

    pkg, pv, warnings = await publish_package(
        manifest=manifest_dict,
        publisher_id=user.publisher.id,
        session=session,
        artifact_bytes=artifact_bytes,
    )

    # Schedule async security scan + verification (do NOT block publish response)
    background_tasks.add_task(run_security_scan, pv.id)
    from app.verification.pipeline import run_verification
    background_tasks.add_task(run_verification, pv.id)

    # Fire webhook event (after commit in publish_package)
    await fire_event(session, user.publisher.id, "version.published", {
        "slug": pkg.slug, "version": pv.version_number, "package_type": pkg.package_type,
    })

    message = f"Published {pkg.slug}@{pv.version_number}"
    if warnings:
        message += " (with warnings: " + "; ".join(warnings) + ")"

    return PublishResponse(
        slug=pkg.slug,
        version=pv.version_number,
        package_type=pkg.package_type,
        message=message,
    )


def _version_eager_loads():
    """Common selectinload options for PackageVersion relationships."""
    from app.verification.models import VerificationResult
    return [
        selectinload(PackageVersion.capabilities),
        selectinload(PackageVersion.compatibility_rules),
        selectinload(PackageVersion.dependencies),
        selectinload(PackageVersion.permissions),
        selectinload(PackageVersion.upgrade_metadata),
        selectinload(PackageVersion.security_findings),
        selectinload(PackageVersion.latest_verification_result),
        selectinload(PackageVersion.tags),
    ]


@router.get("/{slug}", response_model=PackageDetailResponse)
async def get_package(
    slug: str,
    v: str | None = Query(None, description="Specific version number to load"),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Package)
        .options(
            selectinload(Package.publisher),
            selectinload(Package.latest_version).options(*_version_eager_loads()),
        )
        .where(Package.slug == slug)
    )
    pkg = result.scalar_one_or_none()
    if not pkg:
        raise AppError("PACKAGE_NOT_FOUND", f"Package '{slug}' not found", 404)

    version = None
    quarantine_status = None

    # If specific version requested via ?v=
    if v:
        ver_result = await session.execute(
            select(PackageVersion)
            .options(*_version_eager_loads())
            .where(
                PackageVersion.package_id == pkg.id,
                PackageVersion.version_number == v,
            )
        )
        version = ver_result.scalar_one_or_none()
        if not version:
            raise AppError("PACKAGE_VERSION_NOT_FOUND", f"Version '{v}' not found", 404)
        if version.quarantine_status == "quarantined":
            quarantine_status = "quarantined"
    else:
        version = pkg.latest_version

    # Fallback: if no public latest_version, show the newest quarantined version
    if not version:
        fallback_result = await session.execute(
            select(PackageVersion)
            .options(*_version_eager_loads())
            .where(
                PackageVersion.package_id == pkg.id,
                PackageVersion.quarantine_status == "quarantined",
            )
            .order_by(PackageVersion.published_at.desc())
            .limit(1)
        )
        version = fallback_result.scalar_one_or_none()
        if version:
            quarantine_status = "quarantined"

    # Resolve installable version for install context
    installable_pv, install_reason = await get_latest_installable_version(session, pkg.id)
    installable_version = installable_pv.version_number if installable_pv else None

    return assemble_package_detail(
        pkg, version,
        quarantine_status=quarantine_status,
        installable_version=installable_version,
        install_resolution=install_reason,
    )


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
                verification_status=v.verification_status,
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
                verification_status=v.verification_status,
            )
            for v in versions
        ]
    )


@router.get("/{slug}/versions/{version}/files/{file_path:path}")
async def get_file_preview(
    slug: str,
    version: str,
    file_path: str,
    session: AsyncSession = Depends(get_session),
):
    """Get a preview of a file from a package version. Served from S3 preview store."""
    import os
    import posixpath

    # --- Path traversal protection ---
    if '\x00' in file_path:
        raise AppError("INVALID_FILE_PATH", "Invalid file path", 400)
    if '..' in file_path:
        raise AppError("INVALID_FILE_PATH", "Invalid file path", 400)
    if file_path.startswith('/'):
        raise AppError("INVALID_FILE_PATH", "Invalid file path", 400)
    normalized = posixpath.normpath(file_path)
    if normalized.startswith('..') or normalized.startswith('/'):
        raise AppError("INVALID_FILE_PATH", "Invalid file path", 400)

    ext = os.path.splitext(file_path)[1].lower()
    if ext not in PREVIEW_EXTENSIONS:
        raise AppError("UNSUPPORTED_TYPE", f"File type '{ext}' not previewable", 415)

    # Look up version to get its ID
    result = await session.execute(
        select(Package).where(Package.slug == slug)
    )
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
        raise AppError("PACKAGE_VERSION_NOT_FOUND", f"Version '{version}' not found", 404)
    if pv.quarantine_status == "quarantined":
        raise AppError("VERSION_QUARANTINED", "This version is under review and not yet accessible", 403)
    version_id = pv.id

    content = download_preview_file(str(version_id), file_path)
    if content is None:
        raise AppError("FILE_NOT_FOUND", f"File '{file_path}' not found in preview store", 404)

    content_type_map = {
        ".md": "text/markdown", ".py": "text/x-python", ".ts": "text/typescript",
        ".js": "text/javascript", ".json": "application/json", ".yaml": "text/yaml",
        ".yml": "text/yaml", ".toml": "text/toml", ".txt": "text/plain",
        ".cfg": "text/plain", ".ini": "text/plain",
    }
    ct = content_type_map.get(ext, "text/plain")

    return PlainTextResponse(
        content=content,
        media_type=ct,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "ETag": f'"{version_id}:{file_path}"',
        },
    )


@router.patch("/{slug}", response_model=ActionResponse, dependencies=[Depends(rate_limit(20, 60))])
async def update_package(
    slug: str,
    body: UpdatePackageRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Edit package metadata. Owner-only."""
    result = await session.execute(
        select(Package).options(selectinload(Package.publisher)).where(Package.slug == slug)
    )
    pkg = result.scalar_one_or_none()
    if not pkg:
        raise AppError("PACKAGE_NOT_FOUND", f"Package '{slug}' not found", 404)
    if pkg.publisher.user_id != user.id:
        raise AppError("PACKAGE_NOT_OWNED", "You do not own this package", 403)

    # Validate package-level fields
    if body.name is not None:
        if not body.name.strip():
            raise AppError("INVALID_NAME", "Name must not be empty", 400)
        if len(body.name) > 100:
            raise AppError("INVALID_NAME", "Name must be 100 characters or less", 400)
    if body.summary is not None:
        if not body.summary.strip():
            raise AppError("INVALID_SUMMARY", "Summary must not be empty", 400)
        if len(body.summary) > 200:
            raise AppError("INVALID_SUMMARY", "Summary must be 200 characters or less", 400)
    if body.description is not None and len(body.description) > 5000:
        raise AppError("INVALID_DESCRIPTION", "Description must be 5000 characters or less", 400)
    if body.tags is not None and len(body.tags) > 20:
        raise AppError("INVALID_TAGS", "Maximum 20 tags allowed", 400)

    # Apply package-level updates
    search_fields_changed = False
    if body.name is not None:
        pkg.name = body.name.strip()
        search_fields_changed = True
    if body.summary is not None:
        pkg.summary = body.summary.strip()
        search_fields_changed = True
    if body.description is not None:
        pkg.description = body.description.strip() or None
        search_fields_changed = True

    # Apply version-level updates (tags only — URLs are immutable after publish)
    pv = None
    if body.tags is not None:
        pv = await get_latest_owner_visible_version(session, pkg.id)
        if not pv:
            raise AppError("NO_EDITABLE_VERSION", "No editable version found. All versions are yanked.", 409)

        search_fields_changed = True
        # Normalize: strip, lowercase, deduplicate, drop empties
        normalized_tags = list(dict.fromkeys(
            t.strip().lower() for t in body.tags if t.strip()
        ))
        from app.packages.models import PackageTag
        from sqlalchemy import delete
        await session.execute(
            delete(PackageTag).where(PackageTag.package_version_id == pv.id)
        )
        for tag in normalized_tags:
            session.add(PackageTag(package_version_id=pv.id, tag=tag))

    await session.commit()

    # Sync to Meilisearch if search-relevant fields changed
    if search_fields_changed:
        if not pv:
            pv = await get_latest_owner_visible_version(session, pkg.id)
        if pv:
            await session.refresh(pkg, ["publisher"])
            from app.packages.service import build_meili_document
            from app.shared.meili import sync_package_to_meilisearch
            await sync_package_to_meilisearch(build_meili_document(pkg, pv, pv.manifest_raw or {}))

    return ActionResponse()


@router.post("/{slug}/request-reverify", dependencies=[Depends(rate_limit(10, 60))])
async def request_reverify(
    slug: str,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Owner-initiated re-verification of the latest owner-visible version."""
    result = await session.execute(
        select(Package).options(selectinload(Package.publisher)).where(Package.slug == slug)
    )
    pkg = result.scalar_one_or_none()
    if not pkg:
        raise AppError("PACKAGE_NOT_FOUND", f"Package '{slug}' not found", 404)
    if pkg.publisher.user_id != user.id:
        raise AppError("PACKAGE_NOT_OWNED", "You do not own this package", 403)

    pv = await get_latest_owner_visible_version(session, pkg.id)
    if not pv:
        raise AppError("NO_VERSION", "No version available to verify. All versions are yanked.", 409)

    # Guard: already pending/running
    if pv.verification_status in ("pending", "running"):
        raise AppError("VERIFICATION_IN_PROGRESS", "Verification is already in progress for this version.", 409)

    # Guard: cooldown — 1h since last verification
    if pv.last_verified_at:
        cooldown = timedelta(hours=1)
        elapsed = datetime.now(timezone.utc) - pv.last_verified_at.replace(tzinfo=timezone.utc) if pv.last_verified_at.tzinfo is None else datetime.now(timezone.utc) - pv.last_verified_at
        if elapsed < cooldown:
            remaining_mins = int((cooldown - elapsed).total_seconds() / 60)
            raise AppError(
                "COOLDOWN",
                f"Please wait at least 1 hour between verification requests. Try again in ~{remaining_mins} minutes.",
                429,
            )

    # Set status to pending and fire verification
    pv.verification_status = "pending"
    await session.commit()

    from app.verification.pipeline import run_verification
    background_tasks.add_task(run_verification, pv.id, "owner_request")

    return {"message": "Verification requested", "version": pv.version_number}


@router.post("/{slug}/deprecate", response_model=ActionResponse, dependencies=[Depends(rate_limit(max_requests=5, window_seconds=60))])
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

    await fire_event(session, pkg.publisher_id, "package.deprecated", {"slug": pkg.slug})

    # Notify users with active installations
    from app.shared.email import send_package_deprecated_email
    install_results = await session.execute(
        select(User.__table__.c.email).distinct()
        .select_from(Installation.__table__.join(User.__table__, Installation.user_id == User.__table__.c.id))
        .where(Installation.package_id == pkg.id, Installation.status == "active")
    )
    for row in install_results.all():
        await send_package_deprecated_email(row[0], slug)

    return ActionResponse(message="Package deprecated")


@router.post("/{slug}/versions/{version}/yank", response_model=ActionResponse, dependencies=[Depends(rate_limit(max_requests=5, window_seconds=60))])
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

    await fire_event(session, pkg.publisher_id, "version.yanked", {"slug": pkg.slug, "version": version})

    return ActionResponse(message="Version yanked")


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

    # Notify admins
    from app.shared.email import send_report_admin_notification
    await send_report_admin_notification(slug, body.reason, user.username)

    return {"report_id": str(report.id), "status": "submitted"}
