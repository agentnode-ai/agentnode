from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.dependencies import get_current_user, optional_current_user
from app.auth.models import User
from app.database import get_session
from app.packages.models import Installation, Package, PackageVersion
from app.shared.exceptions import AppError
from app.shared.rate_limit import rate_limit, rate_limit_authenticated, _get_client_ip
from app.install.schemas import (
    ArtifactInfo,
    CapabilityInfo,
    DependencyInfo,
    DownloadResponse,
    InstallMetadataResponse,
    InstallRequest,
    InstallResponse,
    PermissionsInfo,
    ToolInfo,
)
from app.install.service import build_artifact_info, get_install_version, track_download, track_install, create_installation
from app.packages.version_queries import get_latest_installable_versions_batch

router = APIRouter(prefix="/v1/packages", tags=["install"])
installations_router = APIRouter(prefix="/v1/installations", tags=["install"])


# --- GET /v1/installations (User's own installations) ---

@installations_router.get("", dependencies=[Depends(rate_limit_authenticated(60, 60))])
async def list_my_installations(
    status: str | None = None,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """List current user's installations."""
    query = (
        select(Installation, Package.slug, PackageVersion.version_number)
        .join(Package, Installation.package_id == Package.id)
        .join(PackageVersion, Installation.package_version_id == PackageVersion.id)
        .where(Installation.user_id == user.id)
    )
    if status:
        query = query.where(Installation.status == status)
    query = query.order_by(Installation.installed_at.desc()).limit(100)

    result = await session.execute(query)
    rows = result.all()

    return {
        "installations": [
            {
                "id": str(inst.id),
                "package_slug": pkg_slug,
                "version": ver_num,
                "status": inst.status,
                "source": inst.source,
                "event_type": inst.event_type,
                "installed_at": inst.installed_at.isoformat() if inst.installed_at else None,
                "activated_at": inst.activated_at.isoformat() if inst.activated_at else None,
                "uninstalled_at": inst.uninstalled_at.isoformat() if inst.uninstalled_at else None,
            }
            for inst, pkg_slug, ver_num in rows
        ],
        "total": len(rows),
    }


@router.get("/{slug}/install-info", response_model=InstallMetadataResponse, dependencies=[Depends(rate_limit(60, 60))])
async def get_install_metadata(
    slug: str,
    version: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    """Get install metadata for a package. Defaults to latest stable version."""
    pkg, pv, reason = await get_install_version(session, slug, version)

    artifact = None
    if pv.artifact_object_key:
        artifact_data = await build_artifact_info(pv)
        artifact = ArtifactInfo(**artifact_data) if artifact_data else None

    capabilities = [
        CapabilityInfo(
            name=c.name,
            capability_id=c.capability_id,
            capability_type=c.capability_type,
            entrypoint=c.entrypoint,
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
        verification_status=pv.verification_status,
        verification_tier=pv.verification_tier,
        verification_score=pv.verification_score,
        install_resolution=reason,
    )


@router.post("/{slug}/install", response_model=InstallResponse, dependencies=[Depends(rate_limit_authenticated(60, 60))])
async def install_package(
    slug: str,
    body: InstallRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Create installation record and return artifact URL. Spec §8.6."""
    pkg, pv, reason = await get_install_version(session, slug, body.version)

    artifact_url = None
    artifact_hash = None
    if pv.artifact_object_key:
        artifact_data = await build_artifact_info(pv)
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
        redis = request.app.state.redis
        dedup_key = f"user:{user.id}"
        await track_install(session, pkg.id, version_id=pv.id, redis=redis, dedup_key=dedup_key)

    await session.commit()

    entrypoint = pv.entrypoint
    post_install_code = f"from {entrypoint.rsplit('.', 1)[0]} import tool" if entrypoint else None

    # Build tools list from capabilities with per-tool entrypoints (v0.2)
    tools = [
        ToolInfo(name=c.name, entrypoint=c.entrypoint, capability_id=c.capability_id)
        for c in pv.capabilities
        if c.entrypoint and c.capability_type == "tool"
    ]

    return InstallResponse(
        package_slug=pkg.slug,
        version=pv.version_number,
        artifact_url=artifact_url,
        artifact_hash=artifact_hash,
        entrypoint=entrypoint,
        post_install_code=post_install_code,
        installation_id=str(installation_id),
        deprecated=pkg.is_deprecated,
        tools=tools,
        verification_status=pv.verification_status,
        verification_tier=pv.verification_tier,
        verification_score=pv.verification_score,
        install_resolution=reason,
    )


@router.post("/{slug}/download", response_model=DownloadResponse, dependencies=[Depends(rate_limit(30, 60))])
async def download_package(
    slug: str,
    request: Request,
    version: str | None = None,
    user: User | None = Depends(optional_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Track download and return presigned artifact URL."""
    pkg, pv, reason = await get_install_version(session, slug, version)

    download_url = None
    if pv.artifact_object_key:
        artifact_data = await build_artifact_info(pv)
        download_url = artifact_data["url"] if artifact_data else None

    redis = request.app.state.redis
    dedup_key = f"user:{user.id}" if user else f"ip:{_get_client_ip(request)}"
    new_count = await track_download(session, pkg.id, pv.id, redis=redis, dedup_key=dedup_key)
    await session.commit()

    return DownloadResponse(
        slug=pkg.slug,
        version=pv.version_number,
        download_url=download_url,
        download_count=new_count,
        artifact_hash_sha256=pv.artifact_hash_sha256,
        artifact_size_bytes=pv.artifact_size_bytes,
        verification_tier=pv.verification_tier,
        install_resolution=reason,
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
    # 1. Collect all slugs from the request
    slugs = [p.slug for p in body.packages]

    # 2. Single query: load all packages + their latest published versions
    result = await session.execute(
        select(Package)
        .options(selectinload(Package.latest_version))
        .where(Package.slug.in_(slugs))
    )
    pkg_map = {pkg.slug: pkg for pkg in result.scalars().all()}

    # 3. Single query: load best installable version per package
    found_ids = [pkg.id for pkg in pkg_map.values()]
    installable_map = await get_latest_installable_versions_batch(session, found_ids)

    # 4. Map results back and compare versions
    updates = []
    for pkg_check in body.packages:
        pkg = pkg_map.get(pkg_check.slug)
        if not pkg:
            updates.append({
                "slug": pkg_check.slug,
                "current_version": pkg_check.version,
                "latest_version": None,
                "latest_published_version": None,
                "has_update": False,
                "verification_tier": None,
                "install_resolution": None,
            })
            continue

        installable_entry = installable_map.get(pkg.id)
        if installable_entry:
            installable, reason = installable_entry
            installable_ver = installable.version_number
            installable_tier = installable.verification_tier
        else:
            installable_ver = None
            installable_tier = None
            reason = None

        latest_published = pkg.latest_version.version_number if pkg.latest_version else None

        updates.append({
            "slug": pkg_check.slug,
            "current_version": pkg_check.version,
            "latest_version": installable_ver,
            "latest_published_version": latest_published,
            "has_update": bool(installable_ver and installable_ver != pkg_check.version),
            "verification_tier": installable_tier,
            "install_resolution": reason,
        })

    return {"updates": updates}


# --- POST /v1/installations/{id}/activate (Spec §8.6.3) ---

@installations_router.post("/{installation_id}/activate", dependencies=[Depends(rate_limit_authenticated(30, 60))])
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

    if inst.status not in ("installed", "inactive"):
        raise AppError("INVALID_STATE", f"Cannot activate installation in '{inst.status}' state", 409)

    inst.status = "active"
    inst.activated_at = datetime.now(timezone.utc)
    await session.commit()

    return {"activated": True}


# --- POST /v1/installations/{id}/uninstall (Spec §8.6.3) ---

@installations_router.post("/{installation_id}/uninstall", dependencies=[Depends(rate_limit_authenticated(30, 60))])
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

    # Only decrement if we are actually transitioning from an "active" row.
    # A double-uninstall must not go below zero.
    was_active = inst.status in ("installed", "active")

    inst.status = "uninstalled"
    inst.uninstalled_at = datetime.now(timezone.utc)

    if was_active:
        # P1-D3: keep the denormalized install_count in sync. The cron-based
        # reconcile job still runs nightly as a safety net, but we should not
        # rely on it for basic correctness. GREATEST(install_count-1, 0) guards
        # against ever going negative if the row was already decremented.
        from sqlalchemy import update, func
        from app.packages.models import Package
        await session.execute(
            update(Package)
            .where(Package.id == inst.package_id)
            .values(install_count=func.greatest(Package.install_count - 1, 0))
        )

    await session.commit()

    return {"uninstalled": True}
