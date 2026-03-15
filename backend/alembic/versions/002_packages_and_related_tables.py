"""Packages, versions, capabilities, permissions, compatibility, dependencies, etc.

Revision ID: 002
Revises: 001
Create Date: 2026-03-13
"""
from typing import Sequence, Union

from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enum types
    op.execute("CREATE TYPE package_type AS ENUM ('agent', 'toolpack', 'upgrade')")
    op.execute("CREATE TYPE pricing_model AS ENUM ('free', 'one_time', 'subscription')")
    op.execute("CREATE TYPE version_channel AS ENUM ('stable', 'beta')")
    op.execute("CREATE TYPE runtime_type AS ENUM ('python', 'typescript', 'docker', 'remote', 'mcp')")
    op.execute("CREATE TYPE install_mode AS ENUM ('package', 'remote_endpoint', 'mcp_server')")
    op.execute("CREATE TYPE hosting_type AS ENUM ('self_hosted', 'agentnode_hosted', 'remote')")
    op.execute("CREATE TYPE quarantine_status AS ENUM ('none', 'quarantined', 'cleared', 'rejected')")
    op.execute("CREATE TYPE capability_type AS ENUM ('tool', 'resource', 'prompt')")
    op.execute("CREATE TYPE permission_level AS ENUM ('none', 'restricted', 'unrestricted')")
    op.execute("CREATE TYPE fs_level AS ENUM ('none', 'temp', 'workspace_read', 'workspace_write', 'any')")
    op.execute("CREATE TYPE exec_level AS ENUM ('none', 'limited_subprocess', 'shell')")
    op.execute("CREATE TYPE data_level AS ENUM ('input_only', 'connected_accounts', 'persistent')")
    op.execute("CREATE TYPE approval_level AS ENUM ('always', 'high_risk_only', 'once', 'never')")
    op.execute("CREATE TYPE severity_level AS ENUM ('low', 'medium', 'high', 'critical')")

    # Packages
    op.execute("""
        CREATE TABLE packages (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            publisher_id UUID NOT NULL REFERENCES publishers(id) ON DELETE CASCADE,
            slug TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            package_type package_type NOT NULL,
            summary TEXT NOT NULL,
            description TEXT,
            license_model TEXT DEFAULT 'MIT',
            pricing_model pricing_model NOT NULL DEFAULT 'free',
            is_deprecated BOOLEAN NOT NULL DEFAULT FALSE,
            download_count INTEGER NOT NULL DEFAULT 0,
            latest_version_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_packages_slug ON packages(slug)")
    op.execute("CREATE INDEX idx_packages_publisher ON packages(publisher_id)")
    op.execute("CREATE INDEX idx_packages_type ON packages(package_type)")

    # Package Versions
    op.execute("""
        CREATE TABLE package_versions (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            package_id UUID NOT NULL REFERENCES packages(id) ON DELETE CASCADE,
            version_number TEXT NOT NULL,
            channel version_channel NOT NULL DEFAULT 'stable',
            manifest_raw JSONB NOT NULL,
            runtime runtime_type NOT NULL,
            install_mode install_mode NOT NULL DEFAULT 'package',
            hosting_type hosting_type NOT NULL DEFAULT 'agentnode_hosted',
            entrypoint TEXT,
            changelog TEXT,
            artifact_object_key TEXT,
            artifact_hash_sha256 TEXT,
            artifact_size_bytes BIGINT,
            signature TEXT,
            source_repo_url TEXT,
            source_commit TEXT,
            build_system TEXT,
            quarantine_status quarantine_status NOT NULL DEFAULT 'none',
            quarantined_at TIMESTAMPTZ,
            quarantine_reason TEXT,
            is_yanked BOOLEAN NOT NULL DEFAULT FALSE,
            published_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(package_id, version_number)
        )
    """)
    op.execute("CREATE INDEX idx_versions_package ON package_versions(package_id)")
    op.execute("CREATE INDEX idx_versions_published ON package_versions(published_at DESC)")
    op.execute("CREATE INDEX idx_versions_quarantine ON package_versions(quarantine_status)")

    # FK for latest_version_id
    op.execute("ALTER TABLE packages ADD CONSTRAINT fk_latest_version FOREIGN KEY (latest_version_id) REFERENCES package_versions(id) ON DELETE SET NULL")

    # Capabilities
    op.execute("""
        CREATE TABLE capabilities (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            package_version_id UUID NOT NULL REFERENCES package_versions(id) ON DELETE CASCADE,
            capability_type capability_type NOT NULL,
            capability_id TEXT NOT NULL REFERENCES capability_taxonomy(id),
            name TEXT NOT NULL,
            description TEXT,
            input_schema JSONB,
            output_schema JSONB
        )
    """)
    op.execute("CREATE INDEX idx_capabilities_version ON capabilities(package_version_id)")
    op.execute("CREATE INDEX idx_capabilities_id ON capabilities(capability_id)")

    # Tags & Categories
    op.execute("""
        CREATE TABLE package_tags (
            package_version_id UUID NOT NULL REFERENCES package_versions(id) ON DELETE CASCADE,
            tag TEXT NOT NULL,
            PRIMARY KEY (package_version_id, tag)
        )
    """)
    op.execute("""
        CREATE TABLE package_categories (
            package_version_id UUID NOT NULL REFERENCES package_versions(id) ON DELETE CASCADE,
            category TEXT NOT NULL,
            PRIMARY KEY (package_version_id, category)
        )
    """)

    # Compatibility Rules
    op.execute("""
        CREATE TABLE compatibility_rules (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            package_version_id UUID NOT NULL REFERENCES package_versions(id) ON DELETE CASCADE,
            framework TEXT,
            runtime_version TEXT,
            protocol TEXT
        )
    """)
    op.execute("CREATE INDEX idx_compat_version ON compatibility_rules(package_version_id)")
    op.execute("CREATE INDEX idx_compat_framework ON compatibility_rules(framework)")

    # Dependencies
    op.execute("""
        CREATE TABLE dependencies (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            package_version_id UUID NOT NULL REFERENCES package_versions(id) ON DELETE CASCADE,
            dependency_package_slug TEXT NOT NULL,
            role TEXT,
            is_required BOOLEAN NOT NULL DEFAULT TRUE,
            min_version TEXT,
            fallback_package_slug TEXT
        )
    """)

    # Permissions
    op.execute("""
        CREATE TABLE permissions (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            package_version_id UUID NOT NULL UNIQUE REFERENCES package_versions(id) ON DELETE CASCADE,
            network_level permission_level NOT NULL DEFAULT 'none',
            allowed_domains JSONB DEFAULT '[]',
            filesystem_level fs_level NOT NULL DEFAULT 'none',
            code_execution_level exec_level NOT NULL DEFAULT 'none',
            data_access_level data_level NOT NULL DEFAULT 'input_only',
            user_approval_level approval_level NOT NULL DEFAULT 'never',
            external_integrations JSONB DEFAULT '[]'
        )
    """)

    # Upgrade Metadata
    op.execute("""
        CREATE TABLE upgrade_metadata (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            package_version_id UUID NOT NULL UNIQUE REFERENCES package_versions(id) ON DELETE CASCADE,
            upgrade_roles JSONB DEFAULT '[]',
            recommended_for JSONB DEFAULT '[]',
            replaces_packages JSONB DEFAULT '[]',
            install_strategy TEXT NOT NULL DEFAULT 'local',
            delegation_mode TEXT,
            fallback_behavior TEXT NOT NULL DEFAULT 'skip',
            policy_requirements JSONB DEFAULT '{}'
        )
    """)

    # Security Findings
    op.execute("""
        CREATE TABLE security_findings (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            package_version_id UUID NOT NULL REFERENCES package_versions(id) ON DELETE CASCADE,
            severity severity_level NOT NULL,
            finding_type TEXT NOT NULL,
            description TEXT,
            scanner TEXT,
            is_resolved BOOLEAN NOT NULL DEFAULT FALSE,
            found_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)


def downgrade() -> None:
    for table in [
        "security_findings", "upgrade_metadata", "permissions", "dependencies",
        "compatibility_rules", "package_categories", "package_tags", "capabilities",
    ]:
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("ALTER TABLE packages DROP CONSTRAINT IF EXISTS fk_latest_version")
    op.execute("DROP TABLE IF EXISTS package_versions")
    op.execute("DROP TABLE IF EXISTS packages")
    for enum in [
        "severity_level", "approval_level", "data_level", "exec_level", "fs_level",
        "permission_level", "capability_type", "quarantine_status", "hosting_type",
        "install_mode", "runtime_type", "version_channel", "pricing_model", "package_type",
    ]:
        op.execute(f"DROP TYPE IF EXISTS {enum}")
