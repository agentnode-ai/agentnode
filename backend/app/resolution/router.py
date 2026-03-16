from fastapi import APIRouter, Depends
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
    req = ResolveRequest(
        capabilities=body.capabilities,
        framework=body.framework,
        runtime=body.runtime,
        package_type=body.package_type,
        limit=body.limit,
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

    constraints = PolicyConstraints(
        min_trust=body.policy.min_trust,
        allow_shell=body.policy.allow_shell,
        allow_network=body.policy.allow_network,
    )

    recommended = []
    for s in scored:
        # Load permissions for policy eval
        pkg_result = await session.execute(
            select(Package)
            .options(
                selectinload(Package.publisher),
                selectinload(Package.latest_version)
                .selectinload(PackageVersion.permissions),
                selectinload(Package.latest_version)
                .selectinload(PackageVersion.upgrade_metadata),
            )
            .where(Package.slug == s.slug)
        )
        pkg = pkg_result.scalar_one_or_none()
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

class RecommendRequest(BaseModel):
    missing_capabilities: list[str] = Field(..., min_length=1)
    framework: str | None = None
    runtime: str | None = None


@router.post("/recommend", dependencies=[Depends(rate_limit_authenticated(60, 60))])
async def recommend(
    body: RecommendRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Recommend packages for missing capabilities. Spec §8.5."""
    recommendations = []

    for cap_id in body.missing_capabilities:
        # Validate capability exists in taxonomy
        tax_result = await session.execute(
            select(CapabilityTaxonomy).where(CapabilityTaxonomy.id == cap_id)
        )
        if not tax_result.scalar_one_or_none():
            raise AppError("CAPABILITY_ID_UNKNOWN", f"Unknown capability: {cap_id}", 422)

        # Find packages with this capability
        req = ResolveRequest(
            capabilities=[cap_id],
            framework=body.framework,
            runtime=body.runtime,
            limit=10,
        )
        scored = await resolve(req, session)

        packages = [
            {
                "slug": s.slug,
                "name": s.name,
                "compatibility_score": s.score,
                "trust_level": s.trust_level,
            }
            for s in scored
        ]

        recommendations.append({
            "capability_id": cap_id,
            "packages": packages,
        })

    return {"recommendations": recommendations}


# --- GET /v1/capabilities (public listing) ---

@router.get("/capabilities", dependencies=[Depends(rate_limit(60, 60))])
async def list_capabilities(
    category: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    """List all capabilities in the taxonomy. Public endpoint."""
    query = select(CapabilityTaxonomy).order_by(CapabilityTaxonomy.category, CapabilityTaxonomy.id)
    if category:
        query = query.where(CapabilityTaxonomy.category == category)

    result = await session.execute(query)
    caps = result.scalars().all()

    return {
        "capabilities": [
            {
                "id": c.id,
                "display_name": c.display_name,
                "description": c.description,
                "category": c.category,
            }
            for c in caps
        ],
        "total": len(caps),
    }
