"""Assembles the detail response for a package, including enrichment fields."""
from app.packages.models import Package, PackageVersion
from app.packages.schemas import (
    AgentConfigBlock,
    CapabilityBlock,
    CompatibilityBlock,
    ConnectorBlock,
    EnvironmentInfo,
    EnvRequirement,
    Example,
    FileListItem,
    InstallBlock,
    PackageBlocks,
    PackageDetailResponse,
    PerformanceBlock,
    PermissionsBlock,
    PromptArgumentBlock,
    PromptBlock,
    PublisherInfo,
    RecommendedForBlock,
    ResourceBlock,
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

    # Only expose error_summary for package-side failures (never platform errors)
    error_summary = None
    if vr and status == "failed" and vr.error_summary:
        if not vr.error_summary.startswith("[PLATFORM]"):
            error_summary = vr.error_summary

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
        error_summary=error_summary,
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
    blocks_prompts: list[PromptBlock] = []
    blocks_resources: list[ResourceBlock] = []
    blocks_connector: ConnectorBlock | None = None
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
    agent_config = None

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

        # Prompts block — extract from manifest_raw (template + arguments live there)
        raw_prompts = (version.manifest_raw or {}).get("capabilities", {}).get("prompts", [])
        for p in raw_prompts:
            if not p.get("name") or not p.get("template"):
                continue
            args = None
            if p.get("arguments"):
                args = [
                    PromptArgumentBlock(
                        name=a["name"],
                        description=a.get("description"),
                        required=a.get("required", False),
                    )
                    for a in p["arguments"]
                    if a.get("name")
                ]
            blocks_prompts.append(PromptBlock(
                name=p["name"],
                capability_id=p.get("capability_id", ""),
                template=p["template"],
                description=p.get("description"),
                arguments=args,
            ))

        # Resources block — extract from manifest_raw (uri + mime_type live there)
        raw_resources = (version.manifest_raw or {}).get("capabilities", {}).get("resources", [])
        for r in raw_resources:
            if not r.get("name") or not r.get("uri"):
                continue
            blocks_resources.append(ResourceBlock(
                name=r["name"],
                capability_id=r.get("capability_id", ""),
                uri=r["uri"],
                description=r.get("description"),
                mime_type=r.get("mime_type"),
            ))

        # Connector block — extract from manifest_raw
        raw_connector = (version.manifest_raw or {}).get("connector")
        if raw_connector and isinstance(raw_connector, dict) and raw_connector.get("provider"):
            health = raw_connector.get("health_check", {})
            rate = raw_connector.get("rate_limits", {})
            blocks_connector = ConnectorBlock(
                provider=raw_connector["provider"],
                auth_type=raw_connector.get("auth_type"),
                scopes=raw_connector.get("scopes", []),
                token_refresh=raw_connector.get("token_refresh", False),
                health_check_endpoint=health.get("endpoint") if isinstance(health, dict) else None,
                rate_limit_rpm=rate.get("requests_per_minute") if isinstance(rate, dict) else None,
            )

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
        runtime = version.runtime or "python"
        blocks_compat = CompatibilityBlock(frameworks=frameworks, runtime=runtime, python=python_ver, dependencies=deps)

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

        # Agent config block
        raw_agent = (version.manifest_raw or {}).get("agent")
        if raw_agent and isinstance(raw_agent, dict):
            limits = raw_agent.get("limits", {})
            if not isinstance(limits, dict):
                limits = {}
            tool_access = raw_agent.get("tool_access", {})
            if not isinstance(tool_access, dict):
                tool_access = {}
            agent_config = AgentConfigBlock(
                goal=raw_agent.get("goal"),
                entrypoint=raw_agent.get("entrypoint"),
                allowed_packages=tool_access.get("allowed_packages"),
                max_iterations=limits.get("max_iterations"),
                max_tool_calls=limits.get("max_tool_calls"),
                max_runtime_seconds=limits.get("max_runtime_seconds"),
                isolation=raw_agent.get("isolation"),
                system_prompt=raw_agent.get("system_prompt"),
                tier=raw_agent.get("tier"),
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
        install_count=pkg.install_count,
        is_deprecated=pkg.is_deprecated,
        quarantine_status=quarantine_status,
        blocks=PackageBlocks(
            capabilities=blocks_caps,
            prompts=blocks_prompts,
            resources=blocks_resources,
            connector=blocks_connector,
            recommended_for=blocks_recommended,
            install=blocks_install,
            compatibility=blocks_compat,
            permissions=blocks_perms,
            performance=PerformanceBlock(download_count=pkg.download_count, install_count=pkg.install_count),
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
        agent_config=agent_config,
    )
