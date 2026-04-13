"""Publish service — creates packages and versions from validated manifests."""
from __future__ import annotations

import hashlib
import io
import logging
import os
import tarfile
from datetime import datetime, timezone
from uuid import UUID

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.packages.models import (
    Capability,
    CapabilityTaxonomy,
    CompatibilityRule,
    Dependency,
    Package,
    PackageCategory,
    PackageTag,
    PackageVersion,
    Permission,
    UpgradeMetadata,
)
from app.packages.typosquatting import find_similar_slugs_db
from app.packages.validator import normalize_manifest, validate_manifest, validate_artifact_quality
from app.packages.version_queries import recalculate_latest_version_id
from app.shared.exceptions import AppError
from app.shared.validators import is_safe_url
from app.shared.meili import sync_package_to_meilisearch
from app.shared.storage import (
    upload_artifact,
    upload_preview_file,
    PREVIEW_EXTENSIONS,
    PREVIEW_MAX_BYTES,
    PREVIEW_MAX_LINES,
)

logger = logging.getLogger(__name__)


def _is_safe_provenance_url(url: str | None) -> bool:
    """Validate provenance URLs — block private IPs to prevent SSRF."""
    if not url:
        return False
    return is_safe_url(url, block_private=True)


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
        "install_count": pkg.install_count,
        "is_deprecated": pkg.is_deprecated,
        "verification_status": version.verification_status,
        "verification_score": version.verification_score or 0,
        "verification_tier": version.verification_tier or "unverified",
        "published_at": version.published_at.isoformat() if version.published_at else None,
        "has_security_review": version.security_reviewed_at is not None,
        "has_compatibility_review": version.compatibility_reviewed_at is not None,
        "has_manual_review": version.manually_reviewed_at is not None,
    }


async def extract_artifact_metadata(artifact_bytes: bytes, version_id: str | None = None) -> dict:
    """Extract file_list and readme_md from a tar.gz artifact.

    Also uploads preview files to S3 if version_id is provided.
    Returns {"file_list": [...], "readme_md": str | None, "preview_keys": [...]}.
    """
    result: dict = {"file_list": [], "readme_md": None, "preview_keys": []}

    try:
        with tarfile.open(fileobj=io.BytesIO(artifact_bytes), mode="r:gz") as tar:
            for member in tar.getmembers():
                if member.isdir():
                    continue

                # Normalize path (strip leading prefix dir)
                path = member.name
                parts = path.split("/", 1)
                normalized = parts[1] if len(parts) > 1 else parts[0]

                result["file_list"].append({
                    "path": normalized,
                    "size": member.size,
                })

                # Extract README.md (case-insensitive)
                basename = os.path.basename(normalized).lower()
                if basename == "readme.md" and "/" not in normalized:
                    f = tar.extractfile(member)
                    if f:
                        raw = f.read()
                        # Safety cut at 1MB
                        if len(raw) <= 1_000_000:
                            try:
                                result["readme_md"] = raw.decode("utf-8")
                            except UnicodeDecodeError:
                                pass

                # Upload preview files
                if version_id:
                    ext = os.path.splitext(normalized)[1].lower()
                    if ext in PREVIEW_EXTENSIONS and member.size <= PREVIEW_MAX_BYTES:
                        f = tar.extractfile(member)
                        if f:
                            raw = f.read()
                            # Binary detection via null byte
                            if b"\x00" not in raw:
                                try:
                                    content = raw.decode("utf-8")
                                    # Limit lines
                                    lines = content.splitlines(True)
                                    if len(lines) > PREVIEW_MAX_LINES:
                                        content = "".join(lines[:PREVIEW_MAX_LINES])
                                    key = await upload_preview_file(version_id, normalized, content)
                                    result["preview_keys"].append(key)
                                except UnicodeDecodeError:
                                    pass
    except (tarfile.TarError, EOFError):
        logger.warning("Failed to extract artifact metadata")

    return result


async def publish_package(
    manifest: dict,
    publisher_id: UUID,
    session: AsyncSession,
    artifact_bytes: bytes | None = None,
    background_tasks: BackgroundTasks | None = None,
) -> tuple[Package, PackageVersion, list[str]]:
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
    else:
        # No artifact provided — warn publisher clearly
        publish_warnings.append(
            "No artifact uploaded. This package will be published as metadata-only "
            "(not installable). Upload a .tar.gz artifact to make it installable by agents."
        )

    slug = manifest["package_id"]
    version_str = manifest["version"]

    # 2. Check typosquatting — quarantine + warning instead of rejecting (spec §12.3)
    # Uses pg_trgm for efficient DB-level fuzzy matching (no full-table scan)
    similar = await find_similar_slugs_db(slug, session)
    quarantine_for_typosquatting = bool(similar)

    # 2b. Check if this is a new publisher's first package — auto-quarantine
    # Skip quarantine for trusted/curated publishers
    from app.publishers.models import Publisher
    pub_result = await session.execute(
        select(Publisher).where(Publisher.id == publisher_id)
    )
    publisher_obj = pub_result.scalar_one_or_none()
    quarantine_for_new_publisher = False
    is_trusted_publisher = publisher_obj and publisher_obj.trust_level in ("trusted", "curated")
    if publisher_obj and publisher_obj.packages_cleared_count == 0 and not is_trusted_publisher:
        quarantine_for_new_publisher = True

    # Trusted/curated publishers also skip typosquatting quarantine
    if is_trusted_publisher:
        quarantine_for_typosquatting = False

    # 3. Check if package exists
    result = await session.execute(select(Package).where(Package.slug == slug))
    pkg = result.scalar_one_or_none()

    is_new_package = pkg is None
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
        await upload_artifact(artifact_key, artifact_bytes)

    # 5. Provenance & signature verification
    security = manifest.get("security", {})
    provenance = security.get("provenance", {})

    # Verify signature if provided
    signature_verified = False
    if security.get("signature") and artifact_hash:
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
                    raise AppError("PUBLISH_SIGNATURE_INVALID", "Artifact signature verification failed", 400)
            except Exception as e:
                if isinstance(e, AppError):
                    raise
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
        signature_verified=signature_verified,
        source_repo_url=provenance.get("source_repo") if _is_safe_provenance_url(provenance.get("source_repo")) else None,
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
    # 6b. Enrichment fields from manifest
    pv.env_requirements = manifest.get("env_requirements") or None
    pv.use_cases = manifest.get("use_cases") or None
    pv.examples = manifest.get("examples") or None
    pv.homepage_url = manifest.get("homepage_url") or None
    pv.docs_url = manifest.get("docs_url") or None
    pv.source_url = manifest.get("source_url") or None
    pv.readme_md = manifest.get("readme_md") or None

    session.add(pv)
    await session.flush()  # Get pv.id

    # 6c. Extract artifact metadata (file_list, readme_md, preview files)
    # Artifact README overrides manifest readme if present
    if artifact_bytes:
        metadata = await extract_artifact_metadata(artifact_bytes, str(pv.id))
        pv.file_list = metadata.get("file_list") or None
        if metadata.get("readme_md"):
            pv.readme_md = metadata["readme_md"]

    # 7. Auto-create unknown capability_ids in taxonomy
    capabilities = manifest.get("capabilities", {})
    all_cap_ids = set()
    for tool in capabilities.get("tools", []):
        if tool.get("capability_id"):
            all_cap_ids.add(tool["capability_id"])
    for resource in capabilities.get("resources", []):
        if resource.get("capability_id"):
            all_cap_ids.add(resource["capability_id"])
    for prompt in capabilities.get("prompts", []):
        if prompt.get("capability_id"):
            all_cap_ids.add(prompt["capability_id"])

    if all_cap_ids:
        existing = await session.execute(
            select(CapabilityTaxonomy.id).where(CapabilityTaxonomy.id.in_(all_cap_ids))
        )
        existing_ids = {row[0] for row in existing.all()}
        for new_id in all_cap_ids - existing_ids:
            display = new_id.replace("_", " ").title()
            session.add(CapabilityTaxonomy(
                id=new_id,
                display_name=display,
                description=None,
                category="uncategorized",
            ))
            logger.info("Auto-created taxonomy entry '%s' (uncategorized)", new_id)
        await session.flush()

    # 7b. Create capabilities
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
        # Validate recommended_for packages exist AND are owned by this publisher
        rec_for = upgrade.get("recommended_for", [])
        if rec_for:
            from sqlalchemy import func
            existing_count = await session.execute(
                select(func.count()).where(Package.slug.in_(rec_for))
            )
            found = existing_count.scalar() or 0
            if found < len(rec_for):
                raise AppError(
                    "UPGRADE_TARGET_NOT_FOUND",
                    "One or more packages in recommended_for do not exist",
                    422,
                )
            # Ownership check: publisher must own all target packages
            unowned_result = await session.execute(
                select(Package.slug).where(
                    Package.slug.in_(rec_for),
                    Package.publisher_id != publisher_id,
                )
            )
            unowned_slugs = [row[0] for row in unowned_result.all()]
            if unowned_slugs:
                raise AppError(
                    "UPGRADE_OWNERSHIP_VIOLATION",
                    f"You can only create upgrades for your own packages. Not owned: {', '.join(unowned_slugs)}",
                    403,
                )
        # Validate replaces packages exist AND are owned by this publisher
        replaces = upgrade.get("replaces", [])
        if replaces:
            from sqlalchemy import func as sa_func
            replaces_count = await session.execute(
                select(sa_func.count()).where(Package.slug.in_(replaces))
            )
            found_replaces = replaces_count.scalar() or 0
            if found_replaces < len(replaces):
                raise AppError(
                    "UPGRADE_TARGET_NOT_FOUND",
                    "One or more packages in replaces do not exist",
                    422,
                )
            unowned_replaces = await session.execute(
                select(Package.slug).where(
                    Package.slug.in_(replaces),
                    Package.publisher_id != publisher_id,
                )
            )
            unowned_replaces_slugs = [row[0] for row in unowned_replaces.all()]
            if unowned_replaces_slugs:
                raise AppError(
                    "UPGRADE_OWNERSHIP_VIOLATION",
                    f"You can only replace your own packages. Not owned: {', '.join(unowned_replaces_slugs)}",
                    403,
                )
        session.add(UpgradeMetadata(
            package_version_id=pv.id,
            upgrade_roles=upgrade.get("roles", []),
            recommended_for=upgrade.get("recommended_for", []),
            replaces_packages=replaces,
            install_strategy=upgrade.get("install_strategy", "local"),
            delegation_mode=upgrade.get("delegation_mode"),
            fallback_behavior=upgrade.get("fallback_behavior", "skip"),
            policy_requirements=upgrade.get("policy_requirements", {}),
        ))

    # 14. Recalculate latest_version_id
    await recalculate_latest_version_id(session, pkg.id)

    # 14b. Increment publisher's packages_published_count (only for new packages, not new versions)
    if is_new_package:
        from app.publishers.models import Publisher
        pub_obj = await session.get(Publisher, publisher_id)
        if pub_obj:
            pub_obj.packages_published_count = (pub_obj.packages_published_count or 0) + 1

    # 15. Commit (catch concurrent publish of same version — DB unique constraint)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise AppError("VERSION_EXISTS", f"Version {version_str} already exists (concurrent publish)", 409)
    await session.refresh(pkg)

    # 16. Sync to Meilisearch (fire-and-forget, non-blocking)
    if not quarantine_for_typosquatting and not quarantine_for_new_publisher:
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

    # Send publish confirmation email (background if available)
    from app.shared.email import send_package_published_email, get_publisher_email
    pub_email = await get_publisher_email(publisher_id)
    if pub_email:
        if background_tasks:
            background_tasks.add_task(
                send_package_published_email, pub_email, slug, version_str,
                quarantine_for_typosquatting or quarantine_for_new_publisher,
            )
        else:
            await send_package_published_email(
                pub_email, slug, version_str,
                quarantined=(quarantine_for_typosquatting or quarantine_for_new_publisher),
            )

    return pkg, pv, publish_warnings
