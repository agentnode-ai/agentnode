"""API endpoints for verification status."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from app.shared.rate_limit import rate_limit
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.dependencies import optional_current_user
from app.auth.models import User
from app.database import get_session
from app.packages.models import Package, PackageVersion
from app.shared.exceptions import AppError
from app.verification.models import VerificationResult

router = APIRouter(prefix="/v1/packages", tags=["verification"])


class VerificationResponse(BaseModel):
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    # Step statuses: passed/failed/skipped/error/not_present/inconclusive
    install_status: str | None = None
    import_status: str | None = None
    smoke_status: str | None = None
    tests_status: str | None = None
    error_summary: str | None = None
    warnings_count: int = 0
    warnings_summary: str | None = None
    # Runner metadata
    runner_version: str | None = None
    python_version: str | None = None
    runner_platform: str | None = None
    triggered_by: str | None = None
    verification_run_count: int | None = None
    last_verified_at: datetime | None = None
    # Logs only for package owner (redacted for public)
    install_log: str | None = None
    import_log: str | None = None
    smoke_log: str | None = None
    tests_log: str | None = None


def _build_response(
    vr: VerificationResult,
    pv: PackageVersion | None,
    include_logs: bool,
) -> VerificationResponse:
    resp = VerificationResponse(
        status=vr.status,
        started_at=vr.started_at,
        completed_at=vr.completed_at,
        duration_ms=vr.duration_ms,
        install_status=vr.install_status,
        import_status=vr.import_status,
        smoke_status=vr.smoke_status,
        tests_status=vr.tests_status,
        error_summary=vr.error_summary,
        warnings_count=vr.warnings_count or 0,
        warnings_summary=vr.warnings_summary,
        runner_version=vr.runner_version,
        python_version=vr.python_version,
        runner_platform=vr.runner_platform,
        triggered_by=vr.triggered_by,
    )
    if pv:
        resp.verification_run_count = pv.verification_run_count
        resp.last_verified_at = pv.last_verified_at
    if include_logs:
        resp.install_log = vr.install_log
        resp.import_log = vr.import_log
        resp.smoke_log = vr.smoke_log
        resp.tests_log = vr.tests_log
    return resp


@router.get("/{slug}/verification", response_model=VerificationResponse, dependencies=[Depends(rate_limit(60, 60))])
async def get_verification_status(
    slug: str,
    session: AsyncSession = Depends(get_session),
    user: User | None = Depends(optional_current_user),
):
    """Get verification status for the latest version of a package."""
    result = await session.execute(
        select(Package)
        .options(selectinload(Package.publisher))
        .where(Package.slug == slug)
    )
    pkg = result.scalar_one_or_none()
    if not pkg:
        raise AppError("PACKAGE_NOT_FOUND", f"Package '{slug}' not found", 404)

    if not pkg.latest_version_id:
        raise AppError("NO_VERSION", "Package has no published versions", 404)

    # Load the latest version for run_count/last_verified_at
    pv_result = await session.execute(
        select(PackageVersion).where(PackageVersion.id == pkg.latest_version_id)
    )
    pv = pv_result.scalar_one_or_none()

    # Get the latest verification result (most recent run)
    vr_result = await session.execute(
        select(VerificationResult)
        .where(VerificationResult.package_version_id == pkg.latest_version_id)
        .order_by(VerificationResult.created_at.desc())
        .limit(1)
    )
    vr = vr_result.scalar_one_or_none()
    if not vr:
        return VerificationResponse(status="pending")

    is_owner = user and hasattr(user, "publisher") and user.publisher and user.publisher.id == pkg.publisher_id
    return _build_response(vr, pv, include_logs=bool(is_owner))


@router.get("/{slug}/versions/{version}/verification", response_model=VerificationResponse, dependencies=[Depends(rate_limit(60, 60))])
async def get_version_verification_status(
    slug: str,
    version: str,
    session: AsyncSession = Depends(get_session),
    user: User | None = Depends(optional_current_user),
):
    """Get verification status for a specific version."""
    result = await session.execute(
        select(Package)
        .options(selectinload(Package.publisher))
        .where(Package.slug == slug)
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
        raise AppError("VERSION_NOT_FOUND", f"Version '{version}' not found", 404)

    # Get the latest verification result for this version
    vr_result = await session.execute(
        select(VerificationResult)
        .where(VerificationResult.package_version_id == pv.id)
        .order_by(VerificationResult.created_at.desc())
        .limit(1)
    )
    vr = vr_result.scalar_one_or_none()
    if not vr:
        return VerificationResponse(status="pending")

    is_owner = user and hasattr(user, "publisher") and user.publisher and user.publisher.id == pkg.publisher_id
    return _build_response(vr, pv, include_logs=bool(is_owner))
