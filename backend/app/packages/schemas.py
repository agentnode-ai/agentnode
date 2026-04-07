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
    security_reviewed_at: datetime | None = None
    compatibility_reviewed_at: datetime | None = None
    manually_reviewed_at: datetime | None = None


class CapabilityBlock(BaseModel):
    name: str
    capability_id: str
    capability_type: str
    description: str | None
    entrypoint: str | None = None
    input_schema: dict | None = None
    output_schema: dict | None = None


class RecommendedForBlock(BaseModel):
    agent_type: str | None = None
    missing_capability: str | None = None


class InstallBlock(BaseModel):
    cli_command: str
    sdk_code: str
    entrypoint: str | None
    post_install_code: str
    installable_version: str | None = None
    install_resolution: str | None = None


class CompatibilityBlock(BaseModel):
    frameworks: list[str]
    runtime: str = "python"
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
    install_count: int = 0
    review_count: int = 0
    avg_rating: float | None = None


class TrustBlock(BaseModel):
    publisher_trust_level: str
    signature_present: bool
    provenance_present: bool
    security_findings_count: int
    verification_status: str | None = None
    last_updated: datetime | None


# --- Enrichment schemas ---

class FileListItem(BaseModel):
    path: str
    size: int


class EnvRequirement(BaseModel):
    name: str
    required: bool = True
    description: str | None = None


class Example(BaseModel):
    title: str
    language: str = "python"
    code: str


class VerificationStepInfo(BaseModel):
    name: str          # "install", "import", "smoke", "tests"
    status: str        # "passed", "failed", "skipped", "not_present"
    duration_ms: int | None = None


class ScoreBreakdownItem(BaseModel):
    points: int
    max: int
    reason: str


class ScoreExplanation(BaseModel):
    score: int
    tier: str
    confidence: str                    # high/medium/low
    breakdown: dict[str, ScoreBreakdownItem] = {}
    explanation: str = ""


class EnvironmentInfo(BaseModel):
    python_version: str | None = None
    system_capabilities: dict[str, bool] = {}
    sandbox_mode: str | None = None
    installer: str | None = None


class VerificationInfo(BaseModel):
    status: str        # "verified", "failed", "running", "pending", "error", "skipped"
    last_verified_at: datetime | None = None
    runner_version: str | None = None
    steps: list[VerificationStepInfo] = []
    score: int | None = None           # 0-100 verification score
    tier: str | None = None            # "gold", "verified", "partial", "unverified"
    confidence: str | None = None      # "high", "medium", "low"
    score_breakdown: dict | None = None  # Full ScoreResult dict
    smoke_reason: str | None = None
    verification_mode: str | None = None  # "real", "mock", "limited"
    environment: EnvironmentInfo | None = None


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
    install_count: int = 0
    is_deprecated: bool
    quarantine_status: str | None = None
    blocks: PackageBlocks
    # Enrichment fields
    license_model: str | None = None
    readme_md: str | None = None
    file_list: list[FileListItem] | None = None
    env_requirements: list[EnvRequirement] | None = None
    use_cases: list[str] | None = None
    examples: list[Example] | None = None
    tags: list[str] | None = None
    homepage_url: str | None = None
    docs_url: str | None = None
    source_url: str | None = None
    verification: VerificationInfo | None = None


class VersionListItem(BaseModel):
    version_number: str
    channel: str
    changelog: str | None
    published_at: datetime
    quarantine_status: str | None = None
    is_yanked: bool | None = None
    verification_status: str | None = None


class VersionsResponse(BaseModel):
    versions: list[VersionListItem]
    total: int | None = None
    page: int | None = None
    per_page: int | None = None


class PublishResponse(BaseModel):
    slug: str
    version: str
    package_type: str
    message: str


class ActionResponse(BaseModel):
    ok: bool = True
    message: str = ""


class UpdatePackageRequest(BaseModel):
    name: str | None = None          # Package-level
    summary: str | None = None       # Package-level
    description: str | None = None   # Package-level
    tags: list[str] | None = None    # Version-level (latest owner-visible)
    # URLs are set at publish time and immutable for owners.
    # Admin can override via PUT /admin/packages/{slug}.
