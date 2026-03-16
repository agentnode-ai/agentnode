from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.dependencies import get_current_user
from app.auth.models import User
from app.database import get_session
from app.packages.models import Installation, Package, PackageVersion
from app.shared.exceptions import AppError
from app.shared.rate_limit import rate_limit, rate_limit_authenticated
from app.install.schemas import (
    ArtifactInfo,
    CapabilityInfo,
    DependencyInfo,
    DownloadResponse,
    InstallMetadataResponse,
    InstallRequest,
    InstallResponse,
    PermissionsInfo,
)
from app.install.service import build_artifact_info, get_install_version, track_download, create_installation

router = APIRouter(prefix="/v1/packages", tags=["install"])
installations_router = APIRouter(prefix="/v1/installations", tags=["install"])


@router.get("/{slug}/install-info", response_model=InstallMetadataResponse)
async def get_install_metadata(
    slug: str,
    version: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    """Get install metadata for a package. Defaults to latest stable version."""
    pkg, pv = await get_install_version(session, slug, version)

    artifact = None
    if pv.artifact_object_key:
        artifact_data = build_artifact_info(pv)
        artifact = ArtifactInfo(**artifact_data) if artifact_data else None

    capabilities = [
        CapabilityInfo(
            name=c.name,
            capability_id=c.capability_id,
            capability_type=c.capability_type,
        )
        for c in pv.capabilities
    ]

    dependencies = [
        DependencyInfo(
            package_slug=d.dependency_package_slug,
            role=d.role,
            is_required=d.is_required,
            min_version=d.min_version,
        )
        for d in pv.dependencies
    ]

    permissions = None
    if pv.permissions:
        p = pv.permissions
        permissions = PermissionsInfo(
            network_level=p.network_level,
            filesystem_level=p.filesystem_level,
            code_execution_level=p.code_execution_level,
            data_access_level=p.data_access_level,
            user_approval_level=p.user_approval_level,
            allowed_domains=p.allowed_domains or [],
            external_integrations=p.external_integrations or [],
        )

    return InstallMetadataResponse(
        slug=pkg.slug,
        version=pv.version_number,
        package_type=pkg.package_type,
        install_mode=pv.install_mode,
        hosting_type=pv.hosting_type,
        runtime=pv.runtime,
        entrypoint=pv.entrypoint,
        artifact=artifact,
        capabilities=capabilities,
        dependencies=dependencies,
        permissions=permissions,
        published_at=pv.published_at,
    )


@router.post("/{slug}/install", response_model=InstallResponse, dependencies=[Depends(rate_limit_authenticated(60, 60))])
async def install_package(
    slug: str,
    body: InstallRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Create installation record and return artifact URL. Spec §8.6."""
    pkg, pv = await get_install_version(session, slug, body.version)

    artifact_url = None
    artifact_hash = None
    if pv.artifact_object_key:
        artifact_data = build_artifact_info(pv)
        if artifact_data:
            artifact_url = artifact_data["url"]
            artifact_hash = artifact_data["hash_sha256"]

    installation_id = await create_installation(
        session,
        user_id=user.id,
        package_id=pkg.id,
        version_id=pv.id,
        source=body.source,
        event_type=body.event_type,
        context=body.installation_context,
    )

    if body.event_type in ("install", "update"):
        await track_download(session, pkg.id, pv.id)

    entrypoint = pv.entrypoint
    post_install_code = f"from {entrypoint.rsplit('.', 1)[0]} import tool" if entrypoint else None

    return InstallResponse(
        package_slug=pkg.slug,
        version=pv.version_number,
        artifact_url=artifact_url,
        artifact_hash=artifact_hash,
        entrypoint=entrypoint,
        post_install_code=post_install_code,
        installation_id=str(installation_id),
        deprecated=pkg.is_deprecated,
    )


@router.post("/{slug}/download", response_model=DownloadResponse, dependencies=[Depends(rate_limit(30, 60))])
async def download_package(
    slug: str,
    version: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    """Track download and return presigned artifact URL."""
    pkg, pv = await get_install_version(session, slug, version)

    download_url = None
    if pv.artifact_object_key:
        artifact_data = build_artifact_info(pv)
        download_url = artifact_data["url"] if artifact_data else None

    new_count = await track_download(session, pkg.id, pv.id)

    return DownloadResponse(
        slug=pkg.slug,
        version=pv.version_number,
        download_url=download_url,
        download_count=new_count,
    )


# --- POST /v1/packages/check-updates (Spec §8.6.2) ---

class PackageVersionCheck(BaseModel):
    slug: str
    version: str


class CheckUpdatesRequest(BaseModel):
    packages: list[PackageVersionCheck] = Field(..., max_length=100)


@router.post("/check-updates", dependencies=[Depends(rate_limit(60, 60))])
async def check_updates(
    body: CheckUpdatesRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Batch check for available updates. Spec §8.6.2."""
    updates = []
    for pkg_check in body.packages:
        result = await session.execute(
            select(Package)
            .options(
                selectinload(Package.latest_version),
            )
            .where(Package.slug == pkg_check.slug)
        )
        pkg = result.scalar_one_or_none()
        if not pkg or not pkg.latest_version:
            updates.append({
                "slug": pkg_check.slug,
                "current_version": pkg_check.version,
                "latest_version": None,
                "has_update": False,
            })
            continue

        latest = pkg.latest_version.version_number
        updates.append({
            "slug": pkg_check.slug,
            "current_version": pkg_check.version,
            "latest_version": latest,
            "has_update": latest != pkg_check.version,
        })

    return {"updates": updates}


# --- POST /v1/installations/{id}/activate (Spec §8.6.3) ---

@installations_router.post("/{installation_id}/activate")
async def activate_installation(
    installation_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Mark an installation as active. Spec §8.6.3."""
    result = await session.execute(
        select(Installation).where(Installation.id == installation_id)
    )
    inst = result.scalar_one_or_none()
    if not inst:
        raise AppError("INSTALLATION_NOT_FOUND", "Installation not found", 404)
    if inst.user_id != user.id:
        raise AppError("INSTALLATION_NOT_OWNED", "You do not own this installation", 403)

    inst.status = "active"
    inst.activated_at = datetime.now(timezone.utc)
    await session.commit()

    return {"activated": True}


# --- POST /v1/installations/{id}/uninstall (Spec §8.6.3) ---

@installations_router.post("/{installation_id}/uninstall")
async def uninstall_installation(
    installation_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Mark an installation as uninstalled. Spec §8.6.3."""
    result = await session.execute(
        select(Installation).where(Installation.id == installation_id)
    )
    inst = result.scalar_one_or_none()
    if not inst:
        raise AppError("INSTALLATION_NOT_FOUND", "Installation not found", 404)
    if inst.user_id != user.id:
        raise AppError("INSTALLATION_NOT_OWNED", "You do not own this installation", 403)

    inst.status = "uninstalled"
    inst.uninstalled_at = datetime.now(timezone.utc)
    await session.commit()

    return {"uninstalled": True}
