"""Data models for the AgentNode SDK."""
from __future__ import annotations

from dataclasses import dataclass, field


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
