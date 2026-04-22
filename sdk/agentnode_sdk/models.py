"""Data models for the AgentNode SDK."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Capability spec models — non-executable assets (ANP v0.3 taxonomy)
# ---------------------------------------------------------------------------

@dataclass
class PromptArgumentSpec:
    """Typed argument for a prompt template."""

    name: str
    description: str | None = None
    required: bool = False


@dataclass
class PromptSpec:
    """Non-executable prompt template asset.

    Prompts are discoverable assets — no entrypoint, no input_schema.
    """

    name: str
    capability_id: str
    template: str  # required — a prompt without template has no substance
    description: str | None = None
    arguments: list[PromptArgumentSpec] | None = None


@dataclass
class ResourceSpec:
    """Non-executable resource asset.

    Resources are discoverable assets — no entrypoint, no input_schema.
    URI is identity, not a load instruction (S10).
    """

    name: str
    capability_id: str
    uri: str  # required — identity per S10
    description: str | None = None
    mime_type: str | None = None


@dataclass
class SearchHit:
    slug: str
    name: str
    package_type: str
    summary: str
    publisher_slug: str
    trust_level: str
    latest_version: str | None = None
    runtime: str | None = None
    capability_ids: list[str] = field(default_factory=list)
    download_count: int = 0


@dataclass
class SearchResult:
    query: str
    hits: list[SearchHit]
    total: int


@dataclass
class ScoreBreakdown:
    capability: float
    framework: float
    runtime: float
    trust: float
    permissions: float


@dataclass
class ResolvedPackage:
    slug: str
    name: str
    package_type: str
    summary: str
    version: str
    publisher_slug: str
    trust_level: str
    score: float
    breakdown: ScoreBreakdown
    matched_capabilities: list[str] = field(default_factory=list)


@dataclass
class ResolveResult:
    results: list[ResolvedPackage]
    total: int


@dataclass
class ArtifactInfo:
    url: str | None
    hash_sha256: str | None
    size_bytes: int | None


@dataclass
class CapabilityInfo:
    name: str
    capability_id: str
    capability_type: str
    entrypoint: str | None = None


@dataclass
class DependencyInfo:
    package_slug: str
    role: str | None
    is_required: bool
    min_version: str | None = None


@dataclass
class PermissionsInfo:
    network_level: str
    filesystem_level: str
    code_execution_level: str
    data_access_level: str
    user_approval_level: str


@dataclass
class InstallMetadata:
    slug: str
    version: str
    package_type: str
    install_mode: str
    hosting_type: str
    runtime: str
    entrypoint: str | None
    artifact: ArtifactInfo | None
    capabilities: list[CapabilityInfo] = field(default_factory=list)
    dependencies: list[DependencyInfo] = field(default_factory=list)
    permissions: PermissionsInfo | None = None
    agent: dict | None = None


@dataclass
class PackageDetail:
    slug: str
    name: str
    package_type: str
    summary: str
    description: str | None
    download_count: int
    is_deprecated: bool
    latest_version: str | None = None


@dataclass
class InstallResult:
    slug: str
    version: str
    installed: bool
    already_installed: bool
    message: str
    hash_verified: bool = False
    entrypoint: str | None = None
    lockfile_updated: bool = False
    previous_version: str | None = None
    trust_level: str | None = None
    verification_tier: str | None = None


@dataclass
class CanInstallResult:
    allowed: bool
    slug: str
    version: str
    trust_level: str
    reason: str
    permissions: PermissionsInfo | None = None


@dataclass
class RunToolResult:
    """Result of ``run_tool()`` execution."""

    success: bool
    result: Any = None
    error: str | None = None
    mode_used: str = "direct"
    duration_ms: float = 0.0
    timed_out: bool = False
    run_id: str | None = None


@dataclass
class DetectedGap:
    """Result of capability gap detection."""

    capability: str  # e.g. "pdf_extraction"
    confidence: str  # "high" | "medium" | "low"
    source: str  # "import_error" | "error_message" | "context"


@dataclass
class DetectAndInstallResult:
    """Result of detect_and_install() — detection + optional install."""

    detected: bool
    capability: str | None = None
    confidence: str | None = None  # "high" | "medium" | "low"
    installed: bool = False
    install_result: InstallResult | None = None
    auto_upgrade_policy: str | None = None  # "off" | "safe" | "strict"
    error: str | None = None


@dataclass
class SmartRunResult:
    """Result of smart_run() — detection, install, and retry."""

    success: bool
    result: Any = None
    error: str | None = None
    upgraded: bool = False
    installed_slug: str | None = None
    installed_version: str | None = None
    detected_capability: str | None = None
    detection_confidence: str | None = None  # "high" | "medium" | "low"
    auto_upgrade_policy: str | None = None  # "off" | "safe" | "strict"
    duration_ms: float = 0.0
    original_error: str | None = None
