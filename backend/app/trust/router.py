"""Trust endpoint — GET /v1/packages/{slug}/trust
Spec section 8.7"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_session
from app.packages.models import Package, PackageVersion
from app.shared.exceptions import AppError

router = APIRouter(prefix="/v1/packages", tags=["trust"])


@router.get("/{slug}/trust")
async def get_trust_info(
    slug: str,
    session: AsyncSession = Depends(get_session),
):
    """Get trust and security information for a package."""
    result = await session.execute(
        select(Package)
        .options(
            selectinload(Package.publisher),
            selectinload(Package.latest_version)
            .selectinload(PackageVersion.security_findings),
        )
        .where(Package.slug == slug)
    )
    pkg = result.scalar_one_or_none()
    if not pkg:
        raise AppError("PACKAGE_NOT_FOUND", f"Package '{slug}' not found", 404)

    pv = pkg.latest_version
    publisher = pkg.publisher

    # Count security findings
    findings_count = 0
    open_findings = []
    if pv and pv.security_findings:
        for f in pv.security_findings:
            if not f.is_resolved:
                findings_count += 1
                open_findings.append({
                    "severity": f.severity,
                    "finding_type": f.finding_type,
                    "description": f.description,
                })

    return {
        "publisher_trust_level": publisher.trust_level if publisher else "unverified",
        "publisher_slug": publisher.slug if publisher else None,
        "signature_present": bool(pv and pv.signature),
        "provenance_present": bool(pv and pv.source_repo_url),
        "source_repo": pv.source_repo_url if pv else None,
        "security_findings_count": findings_count,
        "open_findings": open_findings,
        "quarantine_status": pv.quarantine_status if pv else None,
        "last_scan_at": None,  # No scan tracking in MVP
    }
