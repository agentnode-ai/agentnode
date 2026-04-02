import json
import logging

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.dependencies import get_current_user
from app.auth.models import User
from app.database import get_session
from app.packages.models import Capability, CapabilityTaxonomy, Package, PackageVersion
from sqlalchemy import func
from app.resolution.engine import ResolveRequest, ScoredPackage, resolve
from app.resolution.policy import PolicyConstraints, PolicyInput, evaluate_policy
from app.shared.exceptions import AppError
from app.shared.rate_limit import rate_limit, rate_limit_authenticated
from app.resolution.schemas import (
    ResolvedPackage,
    ResolveRequestSchema,
    ResolveResponse,
    ScoreBreakdown,
)

router = APIRouter(prefix="/v1", tags=["resolution"])


@router.post("/resolve", response_model=ResolveResponse, dependencies=[Depends(rate_limit_authenticated(60, 60))])
async def resolve_capabilities(
    body: ResolveRequestSchema,
    session: AsyncSession = Depends(get_session),
):
    """Resolve requested capabilities to ranked packages."""
    policy_dict = None
    if body.policy:
        policy_dict = {
            "min_trust": body.policy.min_trust,
            "allow_shell": body.policy.allow_shell,
            "allow_network": body.policy.allow_network,
        }

    req = ResolveRequest(
        capabilities=body.capabilities,
        framework=body.framework,
        runtime=body.runtime,
        package_type=body.package_type,
        limit=body.limit,
        policy=policy_dict,
    )
    scored = await resolve(req, session)

    results = [
        ResolvedPackage(
            slug=s.slug,
            name=s.name,
            package_type=s.package_type,
            summary=s.summary,
            version=s.version,
            publisher_slug=s.publisher_slug,
            trust_level=s.trust_level,
            score=s.score,
            policy_result=s.policy_result,
            breakdown=ScoreBreakdown(**s.breakdown),
            matched_capabilities=s.matched_capabilities,
        )
        for s in scored
    ]

    return ResolveResponse(results=results, total=len(results))


# --- POST /v1/check-policy (Spec §8.5) ---

class PolicySchema(BaseModel):
    min_trust: str | None = None
    allow_shell: bool = True
    allow_network: bool = True


class CheckPolicyRequest(BaseModel):
    package_slug: str
    framework: str | None = None
    runtime: str | None = None
    policy: PolicySchema = Field(default_factory=PolicySchema)


@router.post("/check-policy", dependencies=[Depends(rate_limit_authenticated(60, 60))])
async def check_policy(
    body: CheckPolicyRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Check if a package passes policy constraints. Spec §8.5."""
    result = await session.execute(
        select(Package)
        .options(
            selectinload(Package.publisher),
            selectinload(Package.latest_version)
            .selectinload(PackageVersion.permissions),
        )
        .where(Package.slug == body.package_slug)
    )
    pkg = result.scalar_one_or_none()
    if not pkg:
        raise AppError("PACKAGE_NOT_FOUND", f"Package '{body.package_slug}' not found", 404)

    pv = pkg.latest_version
    if not pv:
        raise AppError("NO_VERSION_AVAILABLE", "No installable version available", 404)

    perm = pv.permissions
    trust_level = pkg.publisher.trust_level if pkg.publisher else "unverified"

    policy_input = PolicyInput(
        trust_level=trust_level,
        network_level=perm.network_level if perm else "none",
        filesystem_level=perm.filesystem_level if perm else "none",
        code_execution_level=perm.code_execution_level if perm else "none",
        data_access_level=perm.data_access_level if perm else "input_only",
        user_approval_level=perm.user_approval_level if perm else "never",
        is_yanked=pv.is_yanked,
        is_quarantined=pv.quarantine_status not in ("none", "cleared"),
    )

    constraints = PolicyConstraints(
        min_trust=body.policy.min_trust,
        allow_shell=body.policy.allow_shell,
        allow_network=body.policy.allow_network,
    )

    policy_result = evaluate_policy(policy_input, constraints)

    return {
        "result": policy_result.result,
        "reasons": policy_result.reasons,
        "package_permissions": {
            "network_level": policy_input.network_level,
            "filesystem_level": policy_input.filesystem_level,
            "code_execution_level": policy_input.code_execution_level,
            "data_access_level": policy_input.data_access_level,
            "user_approval_level": policy_input.user_approval_level,
        },
        "package_trust_level": trust_level,
    }


# --- POST /v1/resolve-upgrade (Spec §8.5) ---

class ResolveUpgradeRequest(BaseModel):
    current_capabilities: list[str] = Field(..., min_length=1)
    framework: str | None = None
    runtime: str | None = None
    policy: PolicySchema = Field(default_factory=PolicySchema)


@router.post("/resolve-upgrade", dependencies=[Depends(rate_limit_authenticated(60, 60))])
async def resolve_upgrade(
    body: ResolveUpgradeRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Find upgrade packages for current capabilities. Spec §8.5."""
    req = ResolveRequest(
        capabilities=body.current_capabilities,
        framework=body.framework,
        runtime=body.runtime,
        package_type="upgrade",
        limit=50,
    )
    scored = await resolve(req, session)

    # Batch-load all packages with relations to avoid N+1 queries
    slugs = [s.slug for s in scored]
    if slugs:
        pkg_results = await session.execute(
            select(Package)
            .options(
                selectinload(Package.publisher),
                selectinload(Package.latest_version)
                .selectinload(PackageVersion.permissions),
                selectinload(Package.latest_version)
                .selectinload(PackageVersion.upgrade_metadata),
            )
            .where(Package.slug.in_(slugs))
        )
        pkg_map = {p.slug: p for p in pkg_results.scalars().all()}
    else:
        pkg_map = {}

    constraints = PolicyConstraints(
        min_trust=body.policy.min_trust,
        allow_shell=body.policy.allow_shell,
        allow_network=body.policy.allow_network,
    )

    recommended = []
    for s in scored:
        pkg = pkg_map.get(s.slug)
        if not pkg or not pkg.latest_version:
            continue

        pv = pkg.latest_version
        perm = pv.permissions
        trust_level = pkg.publisher.trust_level if pkg.publisher else "unverified"

        policy_input = PolicyInput(
            trust_level=trust_level,
            network_level=perm.network_level if perm else "none",
            filesystem_level=perm.filesystem_level if perm else "none",
            code_execution_level=perm.code_execution_level if perm else "none",
            data_access_level=perm.data_access_level if perm else "input_only",
            user_approval_level=perm.user_approval_level if perm else "never",
            is_yanked=pv.is_yanked,
            is_quarantined=pv.quarantine_status not in ("none", "cleared"),
        )

        policy_result = evaluate_policy(policy_input, constraints)

        # Determine risk level
        risk_level = "low"
        if perm:
            if perm.code_execution_level in ("limited_subprocess", "shell"):
                risk_level = "high"
            elif perm.network_level == "unrestricted":
                risk_level = "medium"

        upgrade_role = None
        if pv.upgrade_metadata:
            roles = pv.upgrade_metadata.upgrade_roles
            if roles:
                upgrade_role = roles[0] if isinstance(roles, list) else str(roles)

        recommended.append({
            "package_slug": s.slug,
            "package_name": s.name,
            "version": s.version,
            "compatibility_score": s.score,
            "trust_level": trust_level,
            "risk_level": risk_level,
            "policy_result": policy_result.result,
            "policy_reasons": policy_result.reasons,
            "install_command": f"agentnode install {s.slug}",
            "dependencies": [],
        })

    return {"recommended": recommended}


# --- POST /v1/recommend (Spec §8.5) ---
# Different from /resolve:
#   /resolve  = exact capability matching, strict scoring, policy-aware
#   /recommend = broader discovery, includes related capabilities,
#                filters already-installed packages, explains reasoning

class RecommendRequest(BaseModel):
    missing_capabilities: list[str] = Field(default_factory=list)
    installed_packages: list[str] = Field(default_factory=list)
    agent_description: str | None = None
    framework: str | None = None
    runtime: str | None = None
    limit: int = Field(10, ge=1, le=30)


@router.post("/recommend", dependencies=[Depends(rate_limit_authenticated(60, 60))])
async def recommend(
    body: RecommendRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Recommend packages based on missing capabilities, installed packs, or agent description.

    Unlike /resolve (strict capability matching), /recommend provides broader
    discovery: related capabilities, category-based suggestions, and reasoning.
    """
    if not body.missing_capabilities and not body.agent_description:
        raise AppError(
            "INVALID_REQUEST",
            "Provide missing_capabilities or agent_description",
            422,
        )

    installed_set = set(body.installed_packages)
    recommendations = []

    # 1) Resolve each missing capability with related suggestions
    cap_ids_to_resolve = list(body.missing_capabilities)

    # 2) If agent_description provided, infer capabilities from keywords
    if body.agent_description:
        all_tax = await session.execute(
            select(CapabilityTaxonomy)
        )
        taxonomy = all_tax.scalars().all()
        desc_lower = body.agent_description.lower()
        for cap in taxonomy:
            # Match if description mentions the capability name or keywords
            cap_terms = cap.id.replace("_", " ").split()
            display_terms = cap.display_name.lower().split() if cap.display_name else []
            all_terms = cap_terms + display_terms
            if any(term in desc_lower for term in all_terms if len(term) > 3):
                if cap.id not in cap_ids_to_resolve:
                    cap_ids_to_resolve.append(cap.id)

    # 3) For each capability, find related capabilities in the same category
    seen_caps = set(cap_ids_to_resolve)
    related_caps = []
    if cap_ids_to_resolve:
        tax_result = await session.execute(
            select(CapabilityTaxonomy)
            .where(CapabilityTaxonomy.id.in_(cap_ids_to_resolve))
        )
        requested_taxonomy = tax_result.scalars().all()
        categories = {t.category for t in requested_taxonomy if t.category}

        if categories:
            related_result = await session.execute(
                select(CapabilityTaxonomy)
                .where(
                    CapabilityTaxonomy.category.in_(categories),
                    CapabilityTaxonomy.id.notin_(seen_caps),
                )
            )
            related_caps = [r.id for r in related_result.scalars().all()]

    # 4) Resolve primary capabilities
    for cap_id in cap_ids_to_resolve:
        req = ResolveRequest(
            capabilities=[cap_id],
            framework=body.framework,
            runtime=body.runtime,
            limit=5,
        )
        scored = await resolve(req, session)

        packages = []
        for s in scored:
            if s.slug in installed_set:
                continue
            # Find additional capabilities this package provides
            cap_result = await session.execute(
                select(Capability.capability_id)
                .join(PackageVersion, Capability.package_version_id == PackageVersion.id)
                .join(Package, PackageVersion.package_id == Package.id)
                .where(Package.slug == s.slug)
            )
            all_caps = [row[0] for row in cap_result.all()]
            also_provides = [c for c in all_caps if c != cap_id]

            packages.append({
                "slug": s.slug,
                "name": s.name,
                "version": s.version,
                "compatibility_score": s.score,
                "trust_level": s.trust_level,
                "reason": f"Provides {cap_id} capability"
                + (f" (+ {', '.join(also_provides[:3])})" if also_provides else ""),
                "also_provides": also_provides,
                "install_command": f"agentnode install {s.slug}",
            })

        if packages:
            recommendations.append({
                "capability_id": cap_id,
                "source": "requested",
                "packages": packages,
            })

    # 5) Suggest related capabilities the user didn't ask for
    if related_caps:
        for cap_id in related_caps[:5]:
            req = ResolveRequest(
                capabilities=[cap_id],
                framework=body.framework,
                runtime=body.runtime,
                limit=2,
            )
            scored = await resolve(req, session)
            packages = [
                {
                    "slug": s.slug,
                    "name": s.name,
                    "version": s.version,
                    "compatibility_score": s.score,
                    "trust_level": s.trust_level,
                    "reason": f"Related capability in same category",
                    "also_provides": [],
                    "install_command": f"agentnode install {s.slug}",
                }
                for s in scored
                if s.slug not in installed_set
            ]
            if packages:
                recommendations.append({
                    "capability_id": cap_id,
                    "source": "related",
                    "packages": packages,
                })

    # Deduplicate and limit
    seen_slugs: set[str] = set()
    final: list[dict] = []
    for rec in recommendations:
        deduped_packages = []
        for pkg in rec["packages"]:
            if pkg["slug"] not in seen_slugs:
                seen_slugs.add(pkg["slug"])
                deduped_packages.append(pkg)
        if deduped_packages:
            rec["packages"] = deduped_packages
            final.append(rec)

    return {
        "recommendations": final[:body.limit],
        "total_packages": len(seen_slugs),
    }


# --- GET /v1/capabilities (public listing) ---

logger = logging.getLogger(__name__)


async def invalidate_capabilities_cache(redis) -> None:
    """Clear cached capabilities listings."""
    try:
        keys = await redis.keys("capabilities:*")
        if keys:
            await redis.delete(*keys)
    except Exception:
        logger.warning("Failed to invalidate capabilities cache", exc_info=True)


@router.get("/capabilities", dependencies=[Depends(rate_limit(60, 60))])
async def list_capabilities(
    request: Request,
    category: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    """List all capabilities in the taxonomy. Public endpoint."""
    # Try Redis cache first
    redis = request.app.state.redis
    cache_key = f"capabilities:{category or 'all'}"
    try:
        cached = await redis.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        logger.warning("Redis cache read failed for %s", cache_key, exc_info=True)

    # Subquery: count distinct non-deprecated packages per capability_id
    pkg_count_sq = (
        select(
            Capability.capability_id,
            func.count(func.distinct(Package.id)).label("package_count"),
        )
        .join(PackageVersion, Capability.package_version_id == PackageVersion.id)
        .join(Package, PackageVersion.package_id == Package.id)
        .where(Package.is_deprecated == False)  # noqa: E712
        .group_by(Capability.capability_id)
        .subquery()
    )

    query = (
        select(CapabilityTaxonomy, pkg_count_sq.c.package_count)
        .outerjoin(pkg_count_sq, CapabilityTaxonomy.id == pkg_count_sq.c.capability_id)
        .order_by(CapabilityTaxonomy.category, CapabilityTaxonomy.id)
    )
    if category:
        query = query.where(CapabilityTaxonomy.category == category)

    result = await session.execute(query)
    rows = result.all()

    response = {
        "capabilities": [
            {
                "id": row[0].id,
                "display_name": row[0].display_name,
                "description": row[0].description,
                "category": row[0].category,
                "package_count": row[1] or 0,
            }
            for row in rows
        ],
        "total": len(rows),
    }

    # Cache the response with 5-minute TTL
    try:
        await redis.set(cache_key, json.dumps(response), ex=300)
    except Exception:
        logger.warning("Redis cache write failed for %s", cache_key, exc_info=True)

    return response
