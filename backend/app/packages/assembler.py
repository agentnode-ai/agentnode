"""Assembles the detail response for a package, including enrichment fields."""
from app.packages.models import Package, PackageVersion
from app.packages.schemas import (
    CapabilityBlock,
    CompatibilityBlock,
    EnvironmentInfo,
    EnvRequirement,
    Example,
    FileListItem,
    InstallBlock,
    PackageBlocks,
    PackageDetailResponse,
    PerformanceBlock,
    PermissionsBlock,
    PublisherInfo,
    RecommendedForBlock,
    TrustBlock,
    VerificationInfo,
    VerificationStepInfo,
    VersionInfo,
)


def _build_verification_info(version: PackageVersion) -> VerificationInfo | None:
    """Build VerificationInfo from the latest verification result."""
    vr = version.latest_verification_result if version else None

    # Map verification_status to display status
    status = version.verification_status if version else None
    if not status or status == "pending":
        return VerificationInfo(status=status or "pending")

    # Build environment info from vr.environment_info JSONB
    env_info = None
    if vr and getattr(vr, "environment_info", None):
        env_data = vr.environment_info
        env_info = EnvironmentInfo(
            python_version=env_data.get("python_version"),
            system_capabilities=env_data.get("system_capabilities", {}),
            sandbox_mode=env_data.get("sandbox_mode"),
            installer=env_data.get("installer"),
        )

    info = VerificationInfo(
        status="verified" if status == "passed" else status,
        last_verified_at=version.last_verified_at,
        runner_version=vr.runner_version if vr else None,
        score=vr.verification_score if vr else None,
        tier=vr.verification_tier if vr else None,
        confidence=getattr(vr, "confidence", None) if vr else None,
        score_breakdown=vr.score_breakdown if vr else None,
        smoke_reason=vr.smoke_reason if vr else None,
        verification_mode=getattr(vr, "verification_mode", None) if vr else None,
        environment=env_info,
    )

    if vr:
        step_fields = [
            ("install", vr.install_status, vr.install_duration_ms),
            ("import", vr.import_status, vr.import_duration_ms),
            ("smoke", vr.smoke_status, vr.smoke_duration_ms),
            ("tests", vr.tests_status, vr.tests_duration_ms),
        ]
        for name, step_status, duration in step_fields:
            if step_status:
                info.steps.append(VerificationStepInfo(
                    name=name,
                    status=step_status,
                    duration_ms=duration,
                ))

    return info


def assemble_package_detail(
    pkg: Package,
    version: PackageVersion | None,
    *,
    quarantine_status: str | None = None,
    installable_version: str | None = None,
    install_resolution: str | None = None,
) -> PackageDetailResponse:
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
        installable_version=installable_version,
        install_resolution=install_resolution,
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

    # Enrichment defaults
    readme_md = None
    file_list = None
    env_requirements = None
    use_cases = None
    examples = None
    tags = None
    homepage_url = None
    docs_url = None
    source_url = None
    verification_info = None

    if version:
        version_info = VersionInfo(
            version_number=version.version_number,
            channel=version.channel,
            published_at=version.published_at,
            security_reviewed_at=version.security_reviewed_at,
            compatibility_reviewed_at=version.compatibility_reviewed_at,
            manually_reviewed_at=version.manually_reviewed_at,
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
            installable_version=installable_version,
            install_resolution=install_resolution,
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
            verification_status=version.verification_status,
            last_updated=version.published_at,
        )

        # Enrichment fields
        readme_md = version.readme_md
        if version.file_list:
            file_list = [FileListItem(**f) for f in version.file_list]
        if version.env_requirements:
            env_requirements = [EnvRequirement(**e) for e in version.env_requirements]
        use_cases = version.use_cases
        if version.examples:
            examples = [Example(**e) for e in version.examples]
        tags = [t.tag for t in version.tags] if version.tags else None
        homepage_url = version.homepage_url
        docs_url = version.docs_url
        source_url = version.source_url

        # Verification info
        verification_info = _build_verification_info(version)

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
        quarantine_status=quarantine_status,
        blocks=PackageBlocks(
            capabilities=blocks_caps,
            recommended_for=blocks_recommended,
            install=blocks_install,
            compatibility=blocks_compat,
            permissions=blocks_perms,
            performance=PerformanceBlock(download_count=pkg.download_count),
            trust=blocks_trust,
        ),
        license_model=pkg.license_model,
        readme_md=readme_md,
        file_list=file_list,
        env_requirements=env_requirements,
        use_cases=use_cases,
        examples=examples,
        tags=tags,
        homepage_url=homepage_url,
        docs_url=docs_url,
        source_url=source_url,
        verification=verification_info,
    )
