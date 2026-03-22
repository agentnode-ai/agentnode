from datetime import datetime

from pydantic import BaseModel


class ArtifactInfo(BaseModel):
    url: str | None
    hash_sha256: str | None
    size_bytes: int | None
    expires_in_seconds: int = 900


class DependencyInfo(BaseModel):
    package_slug: str
    role: str | None
    is_required: bool
    min_version: str | None


class CapabilityInfo(BaseModel):
    name: str
    capability_id: str
    capability_type: str
    entrypoint: str | None = None


class PermissionsInfo(BaseModel):
    network_level: str
    filesystem_level: str
    code_execution_level: str
    data_access_level: str
    user_approval_level: str
    allowed_domains: list[str] = []
    external_integrations: list = []


class InstallMetadataResponse(BaseModel):
    slug: str
    version: str
    package_type: str
    install_mode: str
    hosting_type: str
    runtime: str
    entrypoint: str | None
    artifact: ArtifactInfo | None
    capabilities: list[CapabilityInfo]
    dependencies: list[DependencyInfo]
    permissions: PermissionsInfo | None
    published_at: datetime
    verification_status: str | None = None
    verification_tier: str | None = None
    verification_score: int | None = None
    install_resolution: str | None = None


class DownloadResponse(BaseModel):
    slug: str
    version: str
    download_url: str | None
    download_count: int
    artifact_hash_sha256: str | None = None
    artifact_size_bytes: int | None = None
    verification_tier: str | None = None
    install_resolution: str | None = None


class InstallRequest(BaseModel):
    version: str | None = None
    source: str = "cli"
    event_type: str = "install"
    installation_context: dict = {}


class ToolInfo(BaseModel):
    name: str
    entrypoint: str
    capability_id: str


class InstallResponse(BaseModel):
    package_slug: str
    version: str
    install_strategy: str = "local"
    artifact_url: str | None
    artifact_hash: str | None
    entrypoint: str | None
    post_install_code: str | None
    installation_id: str
    deprecated: bool
    tools: list[ToolInfo] = []
    verification_status: str | None = None
    verification_tier: str | None = None
    verification_score: int | None = None
    install_resolution: str | None = None
