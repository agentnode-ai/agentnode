from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ValidateRequest(BaseModel):
    manifest: dict


class ValidateResponse(BaseModel):
    valid: bool
    errors: list[str]
    warnings: list[str]


class PublisherInfo(BaseModel):
    slug: str
    display_name: str
    trust_level: str


class VersionInfo(BaseModel):
    version_number: str
    channel: str
    published_at: datetime


class CapabilityBlock(BaseModel):
    name: str
    capability_id: str
    capability_type: str
    description: str | None


class RecommendedForBlock(BaseModel):
    agent_type: str | None = None
    missing_capability: str | None = None


class InstallBlock(BaseModel):
    cli_command: str
    sdk_code: str
    entrypoint: str | None
    post_install_code: str


class CompatibilityBlock(BaseModel):
    frameworks: list[str]
    python: str | None = None
    dependencies: list[str]


class PermissionsBlock(BaseModel):
    network_level: str
    filesystem_level: str
    code_execution_level: str
    data_access_level: str
    user_approval_level: str


class PerformanceBlock(BaseModel):
    download_count: int
    review_count: int = 0
    avg_rating: float | None = None


class TrustBlock(BaseModel):
    publisher_trust_level: str
    signature_present: bool
    provenance_present: bool
    security_findings_count: int
    last_updated: datetime | None


class PackageBlocks(BaseModel):
    capabilities: list[CapabilityBlock]
    recommended_for: list[RecommendedForBlock]
    install: InstallBlock
    compatibility: CompatibilityBlock
    permissions: PermissionsBlock | None
    performance: PerformanceBlock
    trust: TrustBlock


class PackageDetailResponse(BaseModel):
    slug: str
    name: str
    package_type: str
    summary: str
    description: str | None
    publisher: PublisherInfo
    latest_version: VersionInfo | None
    download_count: int
    is_deprecated: bool
    blocks: PackageBlocks


class VersionListItem(BaseModel):
    version_number: str
    channel: str
    changelog: str | None
    published_at: datetime
    quarantine_status: str | None = None
    is_yanked: bool | None = None


class VersionsResponse(BaseModel):
    versions: list[VersionListItem]


class PublishResponse(BaseModel):
    slug: str
    version: str
    package_type: str
    message: str
