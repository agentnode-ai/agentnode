from sqlalchemy import (
    BigInteger, Boolean, Column, Enum, ForeignKey, Integer, Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import relationship

from app.shared.models import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Package(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "packages"

    publisher_id = Column(UUID(as_uuid=True), ForeignKey("publishers.id", ondelete="CASCADE"), nullable=False, index=True)
    slug = Column(Text, nullable=False, unique=True, index=True)
    name = Column(Text, nullable=False)
    package_type = Column(
        Enum("agent", "toolpack", "upgrade", name="package_type", create_type=False),
        nullable=False,
    )
    summary = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    license_model = Column(Text, default="MIT")
    pricing_model = Column(
        Enum("free", "one_time", "subscription", name="pricing_model", create_type=False),
        nullable=False,
        default="free",
    )
    is_deprecated = Column(Boolean, nullable=False, default=False)
    download_count = Column(Integer, nullable=False, default=0)
    latest_version_id = Column(UUID(as_uuid=True), ForeignKey("package_versions.id", ondelete="SET NULL", use_alter=True, name="fk_latest_version"), nullable=True)

    publisher = relationship("Publisher", backref="packages")
    versions = relationship("PackageVersion", back_populates="package", foreign_keys="PackageVersion.package_id")
    latest_version = relationship("PackageVersion", foreign_keys=[latest_version_id], post_update=True)


class PackageVersion(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "package_versions"
    __table_args__ = (UniqueConstraint("package_id", "version_number"),)

    package_id = Column(UUID(as_uuid=True), ForeignKey("packages.id", ondelete="CASCADE"), nullable=False, index=True)
    version_number = Column(Text, nullable=False)
    channel = Column(
        Enum("stable", "beta", name="version_channel", create_type=False),
        nullable=False,
        default="stable",
    )
    manifest_raw = Column(JSONB, nullable=False)
    runtime = Column(
        Enum("python", "typescript", "docker", "remote", "mcp", name="runtime_type", create_type=False),
        nullable=False,
    )
    install_mode = Column(
        Enum("package", "remote_endpoint", "mcp_server", name="install_mode", create_type=False),
        nullable=False,
        default="package",
    )
    hosting_type = Column(
        Enum("self_hosted", "agentnode_hosted", "remote", name="hosting_type", create_type=False),
        nullable=False,
        default="agentnode_hosted",
    )
    entrypoint = Column(Text, nullable=True)
    changelog = Column(Text, nullable=True)

    # Artifact
    artifact_object_key = Column(Text, nullable=True)
    artifact_hash_sha256 = Column(Text, nullable=True)
    artifact_size_bytes = Column(BigInteger, nullable=True)
    signature = Column(Text, nullable=True)

    # Provenance
    source_repo_url = Column(Text, nullable=True)
    source_commit = Column(Text, nullable=True)
    build_system = Column(Text, nullable=True)

    # Quarantine
    quarantine_status = Column(
        Enum("none", "quarantined", "cleared", "rejected", name="quarantine_status", create_type=False),
        nullable=False,
        default="none",
    )
    quarantined_at = Column(TIMESTAMP(timezone=True), nullable=True)
    quarantine_reason = Column(Text, nullable=True)

    # Verification
    verification_status = Column(
        Enum("pending", "running", "passed", "failed", "error", "skipped",
             name="verification_status", create_type=False),
        nullable=False,
        default="pending",
        server_default="pending",
    )
    latest_verification_result_id = Column(
        UUID(as_uuid=True),
        ForeignKey("verification_results.id", ondelete="SET NULL", use_alter=True, name="fk_latest_verification_result"),
        nullable=True,
    )
    verification_run_count = Column(Integer, nullable=False, default=0, server_default="0")
    last_verified_at = Column(TIMESTAMP(timezone=True), nullable=True)

    # Enrichment (v0.2 — per-version docs + metadata)
    readme_md = Column(Text, nullable=True)
    file_list = Column(JSONB, nullable=True)  # [{"path": "src/tool.py", "size": 1234}]
    env_requirements = Column(JSONB, nullable=True)  # [{"name": "API_KEY", "required": true, "description": "..."}]
    use_cases = Column(JSONB, nullable=True)  # ["Read Excel", "Update ranges"]
    examples = Column(JSONB, nullable=True)  # [{"title": "...", "language": "python", "code": "..."}]
    homepage_url = Column(Text, nullable=True)
    docs_url = Column(Text, nullable=True)
    source_url = Column(Text, nullable=True)
    execution_count = Column(Integer, nullable=False, default=0, server_default="0")
    execution_success_count = Column(Integer, nullable=False, default=0, server_default="0")

    # Phase 4A: Denormalized score/tier for search + display
    verification_score = Column(Integer, nullable=True)
    verification_tier = Column(Text, nullable=True)

    # Lifecycle
    is_yanked = Column(Boolean, nullable=False, default=False)
    published_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default="now()")

    package = relationship("Package", back_populates="versions", foreign_keys=[package_id])
    capabilities = relationship("Capability", back_populates="package_version", cascade="all, delete-orphan")
    tags = relationship("PackageTag", cascade="all, delete-orphan")
    categories = relationship("PackageCategory", cascade="all, delete-orphan")
    compatibility_rules = relationship("CompatibilityRule", cascade="all, delete-orphan")
    dependencies = relationship("Dependency", cascade="all, delete-orphan")
    permissions = relationship("Permission", uselist=False, cascade="all, delete-orphan")
    upgrade_metadata = relationship("UpgradeMetadata", uselist=False, cascade="all, delete-orphan")
    security_findings = relationship("SecurityFinding", cascade="all, delete-orphan")
    verification_results = relationship("VerificationResult", cascade="all, delete-orphan", foreign_keys="VerificationResult.package_version_id")
    latest_verification_result = relationship("VerificationResult", foreign_keys=[latest_verification_result_id], post_update=True, uselist=False)


class Capability(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "capabilities"

    package_version_id = Column(UUID(as_uuid=True), ForeignKey("package_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    capability_type = Column(
        Enum("tool", "resource", "prompt", name="capability_type", create_type=False),
        nullable=False,
    )
    capability_id = Column(Text, ForeignKey("capability_taxonomy.id"), nullable=False, index=True)
    name = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    input_schema = Column(JSONB, nullable=True)
    output_schema = Column(JSONB, nullable=True)
    entrypoint = Column(Text, nullable=True)  # v0.2: per-tool entrypoint (module.path:function)

    package_version = relationship("PackageVersion", back_populates="capabilities")


class PackageTag(Base):
    __tablename__ = "package_tags"

    package_version_id = Column(UUID(as_uuid=True), ForeignKey("package_versions.id", ondelete="CASCADE"), primary_key=True)
    tag = Column(Text, primary_key=True)


class PackageCategory(Base):
    __tablename__ = "package_categories"

    package_version_id = Column(UUID(as_uuid=True), ForeignKey("package_versions.id", ondelete="CASCADE"), primary_key=True)
    category = Column(Text, primary_key=True)


class CompatibilityRule(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "compatibility_rules"

    package_version_id = Column(UUID(as_uuid=True), ForeignKey("package_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    framework = Column(Text, nullable=True, index=True)
    runtime_version = Column(Text, nullable=True)
    protocol = Column(Text, nullable=True)


class Dependency(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "dependencies"

    package_version_id = Column(UUID(as_uuid=True), ForeignKey("package_versions.id", ondelete="CASCADE"), nullable=False)
    dependency_package_slug = Column(Text, nullable=False)
    role = Column(Text, nullable=True)
    is_required = Column(Boolean, nullable=False, default=True)
    min_version = Column(Text, nullable=True)
    fallback_package_slug = Column(Text, nullable=True)


class Permission(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "permissions"

    package_version_id = Column(UUID(as_uuid=True), ForeignKey("package_versions.id", ondelete="CASCADE"), nullable=False, unique=True)
    network_level = Column(
        Enum("none", "restricted", "unrestricted", name="permission_level", create_type=False),
        nullable=False, default="none",
    )
    allowed_domains = Column(JSONB, default=[])
    filesystem_level = Column(
        Enum("none", "temp", "workspace_read", "workspace_write", "any", name="fs_level", create_type=False),
        nullable=False, default="none",
    )
    code_execution_level = Column(
        Enum("none", "limited_subprocess", "shell", name="exec_level", create_type=False),
        nullable=False, default="none",
    )
    data_access_level = Column(
        Enum("input_only", "connected_accounts", "persistent", name="data_level", create_type=False),
        nullable=False, default="input_only",
    )
    user_approval_level = Column(
        Enum("always", "high_risk_only", "once", "never", name="approval_level", create_type=False),
        nullable=False, default="never",
    )
    external_integrations = Column(JSONB, default=[])


class UpgradeMetadata(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "upgrade_metadata"

    package_version_id = Column(UUID(as_uuid=True), ForeignKey("package_versions.id", ondelete="CASCADE"), nullable=False, unique=True)
    upgrade_roles = Column(JSONB, default=[])
    recommended_for = Column(JSONB, default=[])
    replaces_packages = Column(JSONB, default=[])
    install_strategy = Column(Text, nullable=False, default="local")
    delegation_mode = Column(Text, nullable=True)
    fallback_behavior = Column(Text, nullable=False, default="skip")
    policy_requirements = Column(JSONB, default={})


class SecurityFinding(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "security_findings"

    package_version_id = Column(UUID(as_uuid=True), ForeignKey("package_versions.id", ondelete="CASCADE"), nullable=False)
    severity = Column(
        Enum("low", "medium", "high", "critical", name="severity_level", create_type=False),
        nullable=False,
    )
    finding_type = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    scanner = Column(Text, nullable=True)
    is_resolved = Column(Boolean, nullable=False, default=False)
    found_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default="now()")


class Installation(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "installations"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    package_id = Column(UUID(as_uuid=True), ForeignKey("packages.id", ondelete="CASCADE"), nullable=False, index=True)
    package_version_id = Column(UUID(as_uuid=True), ForeignKey("package_versions.id", ondelete="CASCADE"), nullable=False)
    source = Column(
        Enum("cli", "api", "web", "sdk", "adapter", name="install_source", create_type=False),
        nullable=False,
    )
    status = Column(
        Enum("installed", "active", "failed", "uninstalled", name="install_status", create_type=False),
        nullable=False,
        default="installed",
    )
    event_type = Column(
        Enum("install", "update", "rollback", name="install_event_type", create_type=False),
        nullable=False,
        default="install",
    )
    installation_context = Column(JSONB, default={})
    installed_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default="now()")
    activated_at = Column(TIMESTAMP(timezone=True), nullable=True)
    uninstalled_at = Column(TIMESTAMP(timezone=True), nullable=True)


class Review(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "reviews"
    __table_args__ = (UniqueConstraint("user_id", "package_id"),)

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    package_id = Column(UUID(as_uuid=True), ForeignKey("packages.id", ondelete="CASCADE"), nullable=False)
    rating = Column(Integer, nullable=False)
    comment = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default="now()")


class PackageReport(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "package_reports"

    package_id = Column(UUID(as_uuid=True), ForeignKey("packages.id"), nullable=False)
    reporter_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    reason = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    status = Column(Text, nullable=False, default="submitted")
    resolved_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    resolution_note = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default="now()")
    resolved_at = Column(TIMESTAMP(timezone=True), nullable=True)


class CapabilityTaxonomy(Base):
    __tablename__ = "capability_taxonomy"

    id = Column(Text, primary_key=True)
    display_name = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    category = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default="now()")
