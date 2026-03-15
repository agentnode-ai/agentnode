"""Resolution Engine — capability-first scoring from spec section 4.3.

Scoring weights:
  capability  0.40  — does the package provide the requested capabilities?
  framework   0.20  — does it support the requested framework?
  runtime     0.15  — does it match the runtime preference?
  trust       0.15  — publisher trust level + provenance signals
  permissions 0.10  — lower permissions = safer = higher score
"""
from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.packages.models import (
    Capability,
    CompatibilityRule,
    Package,
    PackageVersion,
    Permission,
)
from app.packages.version_queries import get_latest_public_version

# Scoring weights
W_CAPABILITY = 0.40
W_FRAMEWORK = 0.20
W_RUNTIME = 0.15
W_TRUST = 0.15
W_PERMISSIONS = 0.10

TRUST_SCORES = {
    "curated": 1.0,
    "trusted": 0.8,
    "verified": 0.6,
    "unverified": 0.3,
}

# Spec §4.3 — direct subtraction penalties for permissions scoring
PERMISSION_DEDUCTIONS = {
    "unrestricted": 0.3,   # network_level == "unrestricted"
    "shell": 0.4,          # code_execution_level == "shell"
    "workspace_write": 0.2,  # filesystem_level == "workspace_write"
    "any": 0.2,            # data_access_level == "any"
}


@dataclass
class ResolveRequest:
    capabilities: list[str]
    framework: str | None = None
    runtime: str | None = None
    package_type: str | None = None
    limit: int = 10


@dataclass
class ScoredPackage:
    slug: str
    name: str
    package_type: str
    summary: str
    version: str
    publisher_slug: str
    trust_level: str
    score: float
    breakdown: dict = field(default_factory=dict)
    matched_capabilities: list[str] = field(default_factory=list)


async def resolve(req: ResolveRequest, session: AsyncSession) -> list[ScoredPackage]:
    """Find and score packages that match the requested capabilities."""

    if not req.capabilities:
        return []

    # Find all package versions that have at least one matching capability
    cap_result = await session.execute(
        select(Capability.package_version_id, Capability.capability_id)
        .where(Capability.capability_id.in_(req.capabilities))
    )
    cap_rows = cap_rows = cap_result.all()

    if not cap_rows:
        return []

    # Group capabilities by version_id
    version_caps: dict[UUID, set[str]] = {}
    for version_id, cap_id in cap_rows:
        version_caps.setdefault(version_id, set()).add(cap_id)

    # Load the package versions with relationships
    version_ids = list(version_caps.keys())
    ver_result = await session.execute(
        select(PackageVersion)
        .options(
            selectinload(PackageVersion.package).selectinload(Package.publisher),
            selectinload(PackageVersion.compatibility_rules),
            selectinload(PackageVersion.permissions),
        )
        .where(
            PackageVersion.id.in_(version_ids),
            PackageVersion.quarantine_status.in_(("none", "cleared")),
            PackageVersion.is_yanked == False,  # noqa: E712
        )
    )
    versions = list(ver_result.scalars().all())

    # For each package, pick the latest public version if multiple match
    best_version_per_package: dict[UUID, PackageVersion] = {}
    for v in versions:
        pkg_id = v.package_id
        if pkg_id not in best_version_per_package:
            best_version_per_package[pkg_id] = v
        else:
            existing = best_version_per_package[pkg_id]
            if v.published_at and existing.published_at and v.published_at > existing.published_at:
                best_version_per_package[pkg_id] = v

    # Score each package
    results: list[ScoredPackage] = []
    requested_caps = set(req.capabilities)

    for pkg_id, version in best_version_per_package.items():
        pkg = version.package
        if not pkg:
            continue
        if pkg.publisher.is_suspended:
            continue
        if req.package_type and pkg.package_type != req.package_type:
            continue

        matched = version_caps.get(version.id, set()) & requested_caps
        if not matched:
            continue

        # Capability score: fraction of requested capabilities matched
        cap_score = len(matched) / len(requested_caps)

        # Framework score
        fw_score = 0.0
        if req.framework:
            frameworks = {r.framework for r in version.compatibility_rules if r.framework}
            if req.framework in frameworks or "generic" in frameworks:
                fw_score = 1.0
            elif frameworks:
                fw_score = 0.3  # has some framework support
        else:
            fw_score = 1.0  # no preference = full score

        # Runtime score
        rt_score = 0.0
        if req.runtime:
            rt_score = 1.0 if version.runtime == req.runtime else 0.0
        else:
            rt_score = 1.0  # no preference = full score

        # Trust score
        trust_score = TRUST_SCORES.get(pkg.publisher.trust_level, 0.3)

        # Permissions score — spec §4.3: start at 1.0, subtract specific penalties
        perm_score = 1.0
        if version.permissions:
            p = version.permissions
            if p.network_level == "unrestricted":
                perm_score -= PERMISSION_DEDUCTIONS["unrestricted"]
            if p.code_execution_level == "shell":
                perm_score -= PERMISSION_DEDUCTIONS["shell"]
            if p.filesystem_level == "workspace_write":
                perm_score -= PERMISSION_DEDUCTIONS["workspace_write"]
            if p.data_access_level == "any":
                perm_score -= PERMISSION_DEDUCTIONS["any"]
            perm_score = max(0.0, perm_score)

        # Final weighted score
        total = (
            W_CAPABILITY * cap_score
            + W_FRAMEWORK * fw_score
            + W_RUNTIME * rt_score
            + W_TRUST * trust_score
            + W_PERMISSIONS * perm_score
        )

        # Deprecated package penalty — spec §12.2: subtract 0.15 instead of excluding
        if pkg.is_deprecated:
            total = max(0.0, total - 0.15)

        results.append(ScoredPackage(
            slug=pkg.slug,
            name=pkg.name,
            package_type=pkg.package_type,
            summary=pkg.summary,
            version=version.version_number,
            publisher_slug=pkg.publisher.slug,
            trust_level=pkg.publisher.trust_level,
            score=round(total, 4),
            breakdown={
                "capability": round(cap_score, 4),
                "framework": round(fw_score, 4),
                "runtime": round(rt_score, 4),
                "trust": round(trust_score, 4),
                "permissions": round(perm_score, 4),
            },
            matched_capabilities=sorted(matched),
        ))

    # Sort by score descending
    results.sort(key=lambda r: -r.score)

    return results[:req.limit]
