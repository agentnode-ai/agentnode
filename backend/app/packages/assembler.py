"""Assembles the 7-block detail response for a package."""
from app.packages.models import Package, PackageVersion
from app.packages.schemas import (
    CapabilityBlock,
    CompatibilityBlock,
    InstallBlock,
    PackageBlocks,
    PackageDetailResponse,
    PerformanceBlock,
    PermissionsBlock,
    PublisherInfo,
    RecommendedForBlock,
    TrustBlock,
    VersionInfo,
)


def assemble_package_detail(pkg: Package, version: PackageVersion | None) -> PackageDetailResponse:
    publisher_info = PublisherInfo(
        slug=pkg.publisher.slug,
        display_name=pkg.publisher.display_name,
        trust_level=pkg.publisher.trust_level,
    )

    version_info = None
    blocks_caps = []
    blocks_recommended = []
    blocks_install = InstallBlock(
        cli_command=f"agentnode install {pkg.slug}",
        sdk_code=f'an.get_install_metadata("{pkg.slug}")',
        entrypoint=None,
        post_install_code="",
    )
    blocks_compat = CompatibilityBlock(frameworks=[], python=None, dependencies=[])
    blocks_perms = None
    blocks_trust = TrustBlock(
        publisher_trust_level=pkg.publisher.trust_level,
        signature_present=False,
        provenance_present=False,
        security_findings_count=0,
        last_updated=None,
    )

    if version:
        version_info = VersionInfo(
            version_number=version.version_number,
            channel=version.channel,
            published_at=version.published_at,
        )

        # Capabilities block
        for cap in version.capabilities:
            blocks_caps.append(CapabilityBlock(
                name=cap.name,
                capability_id=cap.capability_id,
                capability_type=cap.capability_type,
                description=cap.description,
                entrypoint=cap.entrypoint,
                input_schema=cap.input_schema,
                output_schema=cap.output_schema,
            ))

        # Recommended for block
        if version.upgrade_metadata and version.upgrade_metadata.recommended_for:
            for rec in version.upgrade_metadata.recommended_for:
                blocks_recommended.append(RecommendedForBlock(**rec))

        # Install block
        entrypoint = version.entrypoint
        post_install = f"from {entrypoint} import run" if entrypoint else ""
        blocks_install = InstallBlock(
            cli_command=f"agentnode install {pkg.slug}",
            sdk_code=f'an.get_install_metadata("{pkg.slug}")',
            entrypoint=entrypoint,
            post_install_code=post_install,
        )

        # Compatibility block
        frameworks = [r.framework for r in version.compatibility_rules if r.framework]
        python_ver = None
        for r in version.compatibility_rules:
            if r.runtime_version:
                python_ver = r.runtime_version
                break
        deps = [d.dependency_package_slug for d in version.dependencies]
        blocks_compat = CompatibilityBlock(frameworks=frameworks, python=python_ver, dependencies=deps)

        # Permissions block
        if version.permissions:
            p = version.permissions
            blocks_perms = PermissionsBlock(
                network_level=p.network_level,
                filesystem_level=p.filesystem_level,
                code_execution_level=p.code_execution_level,
                data_access_level=p.data_access_level,
                user_approval_level=p.user_approval_level,
            )

        # Trust block
        blocks_trust = TrustBlock(
            publisher_trust_level=pkg.publisher.trust_level,
            signature_present=bool(version.signature),
            provenance_present=bool(version.source_repo_url),
            security_findings_count=len([f for f in version.security_findings if not f.is_resolved]),
            last_updated=version.published_at,
        )

    return PackageDetailResponse(
        slug=pkg.slug,
        name=pkg.name,
        package_type=pkg.package_type,
        summary=pkg.summary,
        description=pkg.description,
        publisher=publisher_info,
        latest_version=version_info,
        download_count=pkg.download_count,
        is_deprecated=pkg.is_deprecated,
        blocks=PackageBlocks(
            capabilities=blocks_caps,
            recommended_for=blocks_recommended,
            install=blocks_install,
            compatibility=blocks_compat,
            permissions=blocks_perms,
            performance=PerformanceBlock(download_count=pkg.download_count),
            trust=blocks_trust,
        ),
    )
