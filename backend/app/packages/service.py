"""Publish service — creates packages and versions from validated manifests."""
import hashlib
import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.packages.models import (
    Capability,
    CompatibilityRule,
    Dependency,
    Package,
    PackageCategory,
    PackageTag,
    PackageVersion,
    Permission,
    UpgradeMetadata,
)
from app.packages.typosquatting import check_typosquatting
from app.packages.validator import normalize_manifest, validate_manifest, validate_artifact_quality
from app.packages.version_queries import recalculate_latest_version_id
from app.shared.exceptions import AppError
from app.shared.meili import sync_package_to_meilisearch
from app.shared.storage import upload_artifact

logger = logging.getLogger(__name__)


async def get_all_package_slugs(session: AsyncSession) -> list[str]:
    result = await session.execute(select(Package.slug))
    return [row[0] for row in result.all()]


def build_meili_document(pkg: Package, version: PackageVersion, manifest: dict) -> dict:
    """Build a Meilisearch document from package + version + manifest."""
    caps = manifest.get("capabilities", {})
    capability_ids = [t["capability_id"] for t in caps.get("tools", [])]
    tags = manifest.get("tags", [])
    frameworks = manifest.get("compatibility", {}).get("frameworks", [])

    return {
        "id": str(pkg.id),
        "slug": pkg.slug,
        "name": pkg.name,
        "package_type": pkg.package_type,
        "summary": pkg.summary,
        "description": pkg.description or "",
        "publisher_name": pkg.publisher.display_name,
        "publisher_slug": pkg.publisher.slug,
        "trust_level": pkg.publisher.trust_level,
        "latest_version": version.version_number,
        "runtime": version.runtime,
        "capability_ids": capability_ids,
        "tags": tags,
        "frameworks": frameworks,
        "download_count": pkg.download_count,
        "is_deprecated": pkg.is_deprecated,
        "verification_status": version.verification_status,
        "published_at": version.published_at.isoformat() if version.published_at else None,
    }


async def publish_package(
    manifest: dict,
    publisher_id: UUID,
    session: AsyncSession,
    artifact_bytes: bytes | None = None,
) -> tuple[Package, PackageVersion]:
    """Full publish flow: validate, check typosquatting, create/update package, create version."""

    # 0. Normalize manifest (v0.2 only — applies defaults to compact manifests)
    manifest = normalize_manifest(manifest)

    # 1. Validate manifest
    valid, errors, warnings = await validate_manifest(manifest, session)
    if not valid:
        raise AppError("MANIFEST_INVALID", "Manifest validation failed", 422, details=errors)

    # 1b. Quality Gate — validate artifact contains tests
    publish_warnings = list(warnings)
    if artifact_bytes:
        qg_errors, qg_warnings = validate_artifact_quality(
            artifact_bytes, manifest.get("package_id", "")
        )
        publish_warnings.extend(qg_warnings)
        if qg_errors:
            raise AppError(
                "QUALITY_GATE_FAILED",
                "Quality gate check failed",
                422,
                details=qg_errors,
            )

    slug = manifest["package_id"]
    version_str = manifest["version"]

    # 2. Check typosquatting — quarantine + warning instead of rejecting (spec §12.3)
    existing_slugs = await get_all_package_slugs(session)
    similar = check_typosquatting(slug, existing_slugs)
    quarantine_for_typosquatting = bool(similar)

    # 2b. Check if this is a new publisher's first package — auto-quarantine
    from app.publishers.models import Publisher
    pub_result = await session.execute(
        select(Publisher).where(Publisher.id == publisher_id)
    )
    publisher_obj = pub_result.scalar_one_or_none()
    quarantine_for_new_publisher = False
    if publisher_obj and publisher_obj.packages_cleared_count == 0:
        quarantine_for_new_publisher = True

    # 3. Check if package exists
    result = await session.execute(select(Package).where(Package.slug == slug))
    pkg = result.scalar_one_or_none()

    if pkg:
        # Existing package — verify ownership
        if pkg.publisher_id != publisher_id:
            raise AppError("PACKAGE_NOT_OWNED", "You do not own this package", 403)

        # Check version doesn't already exist
        ver_result = await session.execute(
            select(PackageVersion).where(
                PackageVersion.package_id == pkg.id,
                PackageVersion.version_number == version_str,
            )
        )
        if ver_result.scalar_one_or_none():
            raise AppError("VERSION_EXISTS", f"Version {version_str} already exists", 409)
    else:
        # New package
        pkg = Package(
            publisher_id=publisher_id,
            slug=slug,
            name=manifest["name"],
            package_type=manifest["package_type"],
            summary=manifest["summary"],
            description=manifest.get("description"),
            license_model=manifest.get("license", "MIT"),
            pricing_model=manifest.get("pricing_model", "free"),
        )
        session.add(pkg)
        await session.flush()  # Get pkg.id

    # 4. Handle artifact
    artifact_key = None
    artifact_hash = None
    artifact_size = None
    if artifact_bytes:
        artifact_hash = hashlib.sha256(artifact_bytes).hexdigest()
        artifact_size = len(artifact_bytes)
        artifact_key = f"artifacts/{slug}/{version_str}/package.tar.gz"
        upload_artifact(artifact_key, artifact_bytes)

    # 5. Provenance & signature verification
    security = manifest.get("security", {})
    provenance = security.get("provenance", {})

    # Verify signature if provided
    signature_verified = False
    if security.get("signature") and artifact_hash:
        publisher_result = await session.execute(
            select(Package.publisher_id).where(Package.id == pkg.id)
        ) if pkg.id else None
        from app.publishers.models import Publisher
        pub_result = await session.execute(
            select(Publisher).where(Publisher.id == publisher_id)
        )
        publisher_obj = pub_result.scalar_one_or_none()
        if publisher_obj and publisher_obj.signing_public_key:
            try:
                from app.trust.signatures import verify_signature
                signature_verified = verify_signature(
                    publisher_obj.signing_public_key,
                    security["signature"],
                    artifact_hash,
                )
                if not signature_verified:
                    logger.warning(f"Signature verification FAILED for {slug}@{version_str}")
            except Exception as e:
                logger.warning(f"Signature verification error for {slug}@{version_str}: {e}")
        elif security.get("signature"):
            logger.info(f"Signature provided but no public key registered for publisher {publisher_id}")

    # 6. Create PackageVersion
    pv = PackageVersion(
        package_id=pkg.id,
        version_number=version_str,
        channel=manifest.get("channel", "stable"),
        manifest_raw=manifest,
        runtime=manifest["runtime"],
        install_mode=manifest.get("install_mode", "package"),
        hosting_type=manifest.get("hosting_type", "agentnode_hosted"),
        entrypoint=manifest.get("entrypoint"),
        changelog=manifest.get("changelog"),
        published_at=datetime.now(timezone.utc),
        artifact_object_key=artifact_key,
        artifact_hash_sha256=artifact_hash,
        artifact_size_bytes=artifact_size,
        signature=security.get("signature"),
        source_repo_url=provenance.get("source_repo"),
        source_commit=provenance.get("commit"),
        build_system=provenance.get("build_system"),
        quarantine_status="quarantined" if (quarantine_for_typosquatting or quarantine_for_new_publisher) else "none",
        quarantine_reason=(
            "typosquatting_suspected" if quarantine_for_typosquatting
            else "new_publisher_review" if quarantine_for_new_publisher
            else None
        ),
        quarantined_at=datetime.now(timezone.utc) if (quarantine_for_typosquatting or quarantine_for_new_publisher) else None,
    )
    session.add(pv)
    await session.flush()  # Get pv.id

    # 7. Create capabilities
    capabilities = manifest.get("capabilities", {})
    for tool in capabilities.get("tools", []):
        session.add(Capability(
            package_version_id=pv.id,
            capability_type="tool",
            capability_id=tool["capability_id"],
            name=tool["name"],
            description=tool.get("description"),
            input_schema=tool.get("input_schema"),
            output_schema=tool.get("output_schema"),
            entrypoint=tool.get("entrypoint"),
        ))
    for resource in capabilities.get("resources", []):
        session.add(Capability(
            package_version_id=pv.id,
            capability_type="resource",
            capability_id=resource["capability_id"],
            name=resource["name"],
            description=resource.get("description"),
            input_schema=resource.get("input_schema"),
            output_schema=resource.get("output_schema"),
        ))
    for prompt in capabilities.get("prompts", []):
        session.add(Capability(
            package_version_id=pv.id,
            capability_type="prompt",
            capability_id=prompt["capability_id"],
            name=prompt["name"],
            description=prompt.get("description"),
        ))

    # 8. Tags
    for tag in manifest.get("tags", []):
        session.add(PackageTag(package_version_id=pv.id, tag=tag))

    # 9. Categories
    for cat in manifest.get("categories", []):
        session.add(PackageCategory(package_version_id=pv.id, category=cat))

    # 10. Compatibility rules
    compat = manifest.get("compatibility", {})
    for fw in compat.get("frameworks", []):
        session.add(CompatibilityRule(
            package_version_id=pv.id,
            framework=fw,
            runtime_version=compat.get("python"),
        ))

    # 11. Dependencies
    for dep in manifest.get("dependencies", []):
        session.add(Dependency(
            package_version_id=pv.id,
            dependency_package_slug=dep.get("package_slug", dep) if isinstance(dep, dict) else dep,
            role=dep.get("role") if isinstance(dep, dict) else None,
            is_required=dep.get("required", True) if isinstance(dep, dict) else True,
            min_version=dep.get("min_version") if isinstance(dep, dict) else None,
            fallback_package_slug=dep.get("fallback") if isinstance(dep, dict) else None,
        ))

    # 12. Permissions
    perms = manifest.get("permissions", {})
    if perms:
        session.add(Permission(
            package_version_id=pv.id,
            network_level=perms.get("network", {}).get("level", "none"),
            allowed_domains=perms.get("network", {}).get("allowed_domains", []),
            filesystem_level=perms.get("filesystem", {}).get("level", "none"),
            code_execution_level=perms.get("code_execution", {}).get("level", "none"),
            data_access_level=perms.get("data_access", {}).get("level", "input_only"),
            user_approval_level=perms.get("user_approval", {}).get("required", "never"),
            external_integrations=perms.get("external_integrations", []),
        ))

    # 13. Upgrade metadata (for package_type == "upgrade")
    if manifest["package_type"] == "upgrade":
        upgrade = manifest.get("upgrade_metadata", {})
        session.add(UpgradeMetadata(
            package_version_id=pv.id,
            upgrade_roles=upgrade.get("roles", []),
            recommended_for=upgrade.get("recommended_for", []),
            replaces_packages=upgrade.get("replaces", []),
            install_strategy=upgrade.get("install_strategy", "local"),
            delegation_mode=upgrade.get("delegation_mode"),
            fallback_behavior=upgrade.get("fallback_behavior", "skip"),
            policy_requirements=upgrade.get("policy_requirements", {}),
        ))

    # 14. Recalculate latest_version_id
    await recalculate_latest_version_id(session, pkg.id)

    # 15. Commit
    await session.commit()
    await session.refresh(pkg)

    # 16. Sync to Meilisearch (fire-and-forget, non-blocking)
    if not quarantine_for_typosquatting:
        await sync_package_to_meilisearch(build_meili_document(pkg, pv, manifest))

    if quarantine_for_typosquatting:
        publish_warnings.append(
            f"Slug '{slug}' is similar to existing packages: {', '.join(similar)}. "
            "Version has been quarantined for review."
        )
    if quarantine_for_new_publisher:
        publish_warnings.append(
            "First-time publisher: version has been quarantined for review. "
            "Once approved, future packages will publish directly."
        )

    # Send publish confirmation email
    from app.shared.email import send_package_published_email, get_publisher_email
    pub_email = await get_publisher_email(publisher_id)
    if pub_email:
        await send_package_published_email(
            pub_email, slug, version_str,
            quarantined=(quarantine_for_typosquatting or quarantine_for_new_publisher),
        )

    return pkg, pv, publish_warnings
