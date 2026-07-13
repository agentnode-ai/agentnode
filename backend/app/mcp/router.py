"""MCP submission endpoints."""

import copy
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_admin, require_publisher
from app.auth.models import User
from app.database import get_session
from app.shared.exceptions import AppError
from app.shared.rate_limit import rate_limit
from app.mcp.models import McpSubmission, PublisherPackageClaim
from app.mcp import status as mcp_status
from app.mcp.gates import evaluate_gates
from app.mcp.registry_verify import (
    verify_registry,
    derive_status,
    normalize_package_name,
)

OWNERSHIP_CLAIM_TTL_DAYS = 180


async def _latest_claim(session: AsyncSession, submission: McpSubmission):
    """Most recent ownership claim (ANY status) matching this submission's
    (publisher, registry, normalized package name). Ownership state = the latest
    claim's effective status; the gate and UI both derive from it, so 'revoked'
    and 'expired' are distinguishable from 'missing'."""
    norm = normalize_package_name(submission.package_registry, submission.package_name)
    return (
        (
            await session.execute(
                select(PublisherPackageClaim)
                .where(
                    PublisherPackageClaim.publisher_id == submission.publisher_id,
                    PublisherPackageClaim.registry == submission.package_registry,
                    PublisherPackageClaim.package_name_normalized == norm,
                )
                .order_by(PublisherPackageClaim.verified_at.desc())
            )
        )
        .scalars()
        .first()
    )


def _ownership_view(claim: PublisherPackageClaim | None) -> dict:
    """Ownership (package_control) status for the admin UI — independent of
    server_verification.repo_consistency."""
    if claim is None:
        return {
            "status": "missing",
            "claim_id": None,
            "method": None,
            "verified_at": None,
            "expires_at": None,
        }
    now = datetime.now(timezone.utc)
    if claim.status == "verified" and (
        claim.expires_at is None or claim.expires_at > now
    ):
        status = "verified"
    elif claim.status == "revoked":
        status = "revoked"
    elif claim.status == "verified" and claim.expires_at and claim.expires_at <= now:
        status = "expired"
    else:
        status = claim.status
    return {
        "status": status,
        "claim_id": str(claim.id),
        "method": claim.method,
        "verified_at": claim.verified_at.isoformat() if claim.verified_at else None,
        "expires_at": claim.expires_at.isoformat() if claim.expires_at else None,
    }


router = APIRouter(prefix="/v1/mcp", tags=["mcp"])
admin_router = APIRouter(prefix="/v1/admin/mcp", tags=["admin-mcp"])


async def _ownership_evidence_for(session, publisher_id, manifest: dict) -> dict:
    """Derive the ownership evidence (Slice 2b-1) for the gate — internal DB read
    only, no external calls. Today no claim can be STRONG (2b-2+ produce those),
    so this never makes the ownership gate pass."""
    from app.mcp.ownership import derive_ownership_evidence

    if publisher_id is None:
        return derive_ownership_evidence(None, "missing")
    mcp = manifest.get("mcp_server") or {}
    name = mcp.get("npm_package") or mcp.get("pypi_package")
    registry = "npm" if mcp.get("npm_package") else "pypi"
    if not name:
        return derive_ownership_evidence(None, "missing")
    norm = normalize_package_name(registry, name)
    claim = (
        (
            await session.execute(
                select(PublisherPackageClaim)
                .where(
                    PublisherPackageClaim.publisher_id == publisher_id,
                    PublisherPackageClaim.registry == registry,
                    PublisherPackageClaim.package_name_normalized == norm,
                )
                .order_by(PublisherPackageClaim.verified_at.desc())
            )
        )
        .scalars()
        .first()
    )
    view = _ownership_view(claim)
    return derive_ownership_evidence(view["method"], view["status"])


async def _attach_gate_result(
    sv: dict, manifest: dict, report: dict, session, *, publisher_id=None
) -> dict:
    """Compute the advisory gate result and store it inside the server_verification
    JSONB (no migration, no status change). Advisory only — activates nothing."""
    from app.packages.typosquatting import find_similar_slugs_db

    slug = (manifest.get("package_id") or "").strip()
    typosquat_hit = False
    if slug:
        try:
            typosquat_hit = bool(await find_similar_slugs_db(slug, session))
        except Exception:
            typosquat_hit = False  # advisory signal only; never block on lookup error
    ownership = await _ownership_evidence_for(session, publisher_id, manifest)
    # 2c-4a: pass the current submission's binding keys so evaluate_gates can
    # downgrade a stale/expired passed smoke (freshness). No I/O; config reads only.
    from app.mcp.smoke_executor import current_smoke_keys

    sv["gate_result"] = evaluate_gates(
        manifest=manifest,
        server_verification=sv,
        report=report or {},
        typosquat_hit=typosquat_hit,
        ownership=ownership,
        # 2c-1 forward-compat: no executor writes sv["smoke"] yet, so this is None
        # today (gate stays a future blocker). When 2c-2 populates it, it flows in.
        smoke=sv.get("smoke"),
        smoke_keys=current_smoke_keys(manifest, sv),
    )
    return sv


logger = logging.getLogger(__name__)

VALID_STATUSES = {
    "INVALID",
    "RESOLVED",
    "TESTED",
    "REVIEW_NEEDED",
    "MAINTAINER_ACTION_REQUIRED",
}
MAX_SUBMIT_BODY_BYTES = 512_000  # 500 KB


class McpSubmitRequest(BaseModel):
    manifest: dict = Field(
        ..., description="agentnode.yaml manifest (v0.3, runtime=mcp)"
    )
    verification_report: dict = Field(
        ..., description="Output of agentnode mcp verify --json"
    )


class McpSubmitResponse(BaseModel):
    id: str
    status: str
    package_name: str
    message: str


@router.post(
    "/submit",
    response_model=McpSubmitResponse,
    status_code=201,
    dependencies=[Depends(rate_limit(5, 60))],
)
async def submit_mcp(
    request: Request,
    body: McpSubmitRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_publisher),
    session: AsyncSession = Depends(get_session),
):
    """Submit an MCP server for catalog review.

    Accepts a manifest and its verification report. The report must have been
    generated by `agentnode mcp verify --json`. No code is executed server-side.
    """
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_SUBMIT_BODY_BYTES:
        raise AppError(
            "MCP_PAYLOAD_TOO_LARGE",
            f"Request body must be under {MAX_SUBMIT_BODY_BYTES // 1024} KB",
            400,
        )

    manifest = body.manifest
    report = body.verification_report

    if manifest.get("runtime") != "mcp":
        raise AppError("MCP_INVALID_RUNTIME", "Manifest runtime must be 'mcp'", 400)

    mcp_server = manifest.get("mcp_server")
    if not isinstance(mcp_server, dict):
        raise AppError("MCP_MISSING_SERVER", "Manifest must have mcp_server block", 400)

    report_status = report.get("status", "")
    if report_status not in VALID_STATUSES:
        raise AppError(
            "MCP_INVALID_REPORT",
            f"Verification report status '{report_status}' is not valid",
            400,
        )

    if report_status == "INVALID":
        raise AppError(
            "MCP_REPORT_INVALID",
            "Cannot submit with INVALID verification status. Fix errors first.",
            400,
        )

    pkg_name = (
        mcp_server.get("npm_package") or mcp_server.get("pypi_package") or "unknown"
    )
    pkg_registry = "npm" if mcp_server.get("npm_package") else "pypi"
    source_repo = mcp_server.get("source_repo")

    # Server-side re-verification: re-derive registry-checkable facts ourselves.
    # The client report is advisory; this is authoritative. No code is executed.
    server_verification = await verify_registry(manifest)
    server_verification = await _attach_gate_result(
        server_verification, manifest, report, session, publisher_id=user.publisher.id
    )
    # Resolved version is authoritative; fall back to the client claim only if the
    # registry was unavailable and we could not resolve anything.
    pkg_version = server_verification.get("resolved_version") or report.get(
        "package", {}
    ).get("version")

    # Duplicate check: block if an open submission exists for same package+version
    open_statuses = tuple(mcp_status.OPEN_STATUSES)
    existing = (
        await session.execute(
            select(McpSubmission).where(
                McpSubmission.publisher_id == user.publisher.id,
                McpSubmission.package_name == pkg_name,
                McpSubmission.package_version == pkg_version,
                McpSubmission.status.in_(open_statuses),
            )
        )
    ).scalar_one_or_none()

    if existing:
        if existing.status == "approved":
            raise AppError(
                "MCP_ALREADY_APPROVED",
                f"{pkg_name}@{pkg_version} was already approved (submission {str(existing.id)[:8]}).",
                409,
                details={
                    "existing_submission_id": str(existing.id),
                    "existing_status": existing.status,
                },
            )
        raise AppError(
            "MCP_DUPLICATE_SUBMISSION",
            f"An open submission for {pkg_name}@{pkg_version} already exists (status: {existing.status}). "
            f"Check status: agentnode mcp status {existing.id}",
            409,
            details={
                "existing_submission_id": str(existing.id),
                "existing_status": existing.status,
            },
        )

    submission = McpSubmission(
        id=uuid4(),
        publisher_id=user.publisher.id,
        package_name=pkg_name,
        package_registry=pkg_registry,
        package_version=pkg_version,
        source_repo=source_repo,
        manifest_raw=manifest,
        verification_report=report,
        server_verification=server_verification,
        status=derive_status(server_verification, report),
    )
    session.add(submission)
    await session.flush()
    await session.commit()

    # 2c-2: schedule the server-authoritative sandbox smoke as a BACKGROUND task
    # (never blocks this response). Inert by default — a cheap no-op unless
    # MCP_SMOKE_MODE=container AND the submission is smoke-eligible.
    from app.mcp.smoke_executor import maybe_schedule_smoke

    maybe_schedule_smoke(background_tasks, submission.id, manifest, server_verification)

    logger.info(
        "MCP submission %s by publisher %s: %s (%s, server=%s)",
        submission.id,
        user.publisher.slug,
        pkg_name,
        submission.status,
        server_verification.get("server_status"),
    )

    if submission.status in mcp_status.REVIEW_HOLD:
        msg = f"Submission received. {pkg_name} is queued for catalog review."
    else:
        sstatus = server_verification.get("server_status")
        if sstatus == "mismatch":
            reasons = (
                "; ".join(server_verification.get("errors") or [])
                or "registry contradicts manifest"
            )
            msg = f"Submission received but server verification failed: {reasons}. Fix and resubmit."
        else:
            actions = report.get("actions", [])
            high_count = sum(1 for a in actions if a.get("severity") == "high")
            msg = f"Submission received with {high_count} required action(s). Fix and resubmit for faster review."

    return McpSubmitResponse(
        id=str(submission.id),
        status=submission.status,
        package_name=pkg_name,
        message=msg,
    )


# ---------------------------------------------------------------------------
# Maintainer list endpoint (publisher-scoped)
# ---------------------------------------------------------------------------


class MaintainerSubmissionSummary(BaseModel):
    id: str
    package_name: str
    package_registry: str
    package_version: str | None
    status: str
    report_status: str | None
    server_status: str | None
    actions_high: int
    actions_medium: int
    maintainer_feedback: str | None
    published_package_id: str | None
    created_at: str


class MaintainerSubmissionListResponse(BaseModel):
    submissions: list[MaintainerSubmissionSummary]
    total: int


@router.get(
    "/submissions",
    response_model=MaintainerSubmissionListResponse,
    dependencies=[Depends(rate_limit(20, 60))],
)
async def list_own_submissions(
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    user: User = Depends(require_publisher),
    session: AsyncSession = Depends(get_session),
):
    """List own MCP submissions, newest first. Publisher-scoped — never
    includes other publishers' submissions or admin-only reviewer notes."""
    own = McpSubmission.publisher_id == user.publisher.id
    query = select(McpSubmission).where(own).order_by(McpSubmission.created_at.desc())
    count_query = select(func.count(McpSubmission.id)).where(own)

    if status:
        query = query.where(McpSubmission.status == status)
        count_query = count_query.where(McpSubmission.status == status)

    total = (await session.execute(count_query)).scalar() or 0
    query = query.offset((page - 1) * per_page).limit(per_page)
    rows = (await session.execute(query)).scalars().all()

    submissions = []
    for s in rows:
        report = s.verification_report or {}
        actions = report.get("actions", [])
        submissions.append(
            MaintainerSubmissionSummary(
                id=str(s.id),
                package_name=s.package_name,
                package_registry=s.package_registry,
                package_version=s.package_version,
                status=s.status,
                report_status=report.get("status"),
                server_status=(s.server_verification or {}).get("server_status"),
                actions_high=sum(1 for a in actions if a.get("severity") == "high"),
                actions_medium=sum(1 for a in actions if a.get("severity") == "medium"),
                maintainer_feedback=s.maintainer_feedback,
                published_package_id=str(s.published_package_id)
                if s.published_package_id
                else None,
                created_at=s.created_at.isoformat() if s.created_at else "",
            )
        )

    return MaintainerSubmissionListResponse(submissions=submissions, total=total)


# ---------------------------------------------------------------------------
# Maintainer status endpoint (publisher-scoped)
# ---------------------------------------------------------------------------


class MaintainerSubmissionStatus(BaseModel):
    id: str
    package_name: str
    package_registry: str
    package_version: str | None
    source_repo: str | None
    status: str
    report_status: str | None
    report_summary: str | None
    actions: list[dict] | None
    server_verification: dict | None
    maintainer_feedback: str | None
    reviewed_at: str | None
    published_package_id: str | None
    created_at: str


@router.get(
    "/submissions/{submission_id}",
    response_model=MaintainerSubmissionStatus,
    dependencies=[Depends(rate_limit(20, 60))],
)
async def get_own_submission(
    submission_id: UUID,
    user: User = Depends(require_publisher),
    session: AsyncSession = Depends(get_session),
):
    """Get status of own submission. Publisher-scoped — only the submitting publisher can view."""
    row = (
        await session.execute(
            select(McpSubmission).where(
                McpSubmission.id == submission_id,
                McpSubmission.publisher_id == user.publisher.id,
            )
        )
    ).scalar_one_or_none()

    if not row:
        raise AppError(
            "MCP_SUBMISSION_NOT_FOUND",
            "Submission not found or not owned by your publisher",
            404,
        )

    report = row.verification_report or {}

    return MaintainerSubmissionStatus(
        id=str(row.id),
        package_name=row.package_name,
        package_registry=row.package_registry,
        package_version=row.package_version,
        source_repo=row.source_repo,
        status=row.status,
        report_status=report.get("status"),
        report_summary=report.get("summary"),
        actions=report.get("actions"),
        server_verification=row.server_verification,
        maintainer_feedback=row.maintainer_feedback,
        reviewed_at=row.reviewed_at.isoformat() if row.reviewed_at else None,
        published_package_id=str(row.published_package_id)
        if row.published_package_id
        else None,
        created_at=row.created_at.isoformat() if row.created_at else "",
    )


class McpReportUpdateRequest(BaseModel):
    verification_report: dict = Field(
        ..., description="Updated output of `agentnode mcp verify --json`"
    )
    manifest: dict | None = Field(
        None, description="Optional updated manifest; keeps the existing one if omitted"
    )


# Statuses from which a maintainer may still attach a fresh report. Approved and
# rejected are terminal for the maintainer; published packages are done.
_REPORT_UPDATABLE_STATUSES = tuple(mcp_status.REPORT_UPDATABLE)


@router.post(
    "/submissions/{submission_id}/report",
    response_model=MaintainerSubmissionStatus,
    dependencies=[Depends(rate_limit(5, 60))],
)
async def update_own_submission_report(
    request: Request,
    submission_id: UUID,
    body: McpReportUpdateRequest,
    user: User = Depends(require_publisher),
    session: AsyncSession = Depends(get_session),
):
    """Attach an updated verification report to an OPEN own submission.

    Unblocks the web-submit path: a submission filed without a CLI-verified
    (TESTED) report lands in ``action_required`` and the duplicate check then
    prevents re-submitting. This lets the maintainer post an updated report
    (e.g. after running ``agentnode mcp verify . --test --json``) to the same
    submission — the server re-verifies against the registry and re-derives the
    status. Publisher-scoped; never touches another publisher's submission and
    never clears admin review state.
    """
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_SUBMIT_BODY_BYTES:
        raise AppError(
            "MCP_PAYLOAD_TOO_LARGE",
            f"Request body must be under {MAX_SUBMIT_BODY_BYTES // 1024} KB",
            400,
        )

    row = (
        await session.execute(
            select(McpSubmission).where(
                McpSubmission.id == submission_id,
                McpSubmission.publisher_id == user.publisher.id,
            )
        )
    ).scalar_one_or_none()
    if not row:
        raise AppError(
            "MCP_SUBMISSION_NOT_FOUND",
            "Submission not found or not owned by your publisher",
            404,
        )

    if row.status not in _REPORT_UPDATABLE_STATUSES:
        raise AppError(
            "MCP_SUBMISSION_NOT_UPDATABLE",
            f"Submission status '{row.status}' cannot accept a new report "
            f"(only {', '.join(_REPORT_UPDATABLE_STATUSES)}).",
            409,
            details={"existing_status": row.status},
        )

    report = body.verification_report
    report_status = report.get("status", "")
    if report_status not in VALID_STATUSES:
        raise AppError(
            "MCP_INVALID_REPORT",
            f"Verification report status '{report_status}' is not valid",
            400,
        )
    if report_status == "INVALID":
        raise AppError(
            "MCP_REPORT_INVALID",
            "Cannot submit an INVALID verification report. Fix errors first.",
            400,
        )

    # Optional manifest swap must stay the same package identity — a report
    # update must never repoint the submission at a different package/version.
    manifest = row.manifest_raw or {}
    if body.manifest is not None:
        new_mcp = body.manifest.get("mcp_server")
        if not isinstance(new_mcp, dict):
            raise AppError(
                "MCP_MISSING_SERVER", "Manifest must have mcp_server block", 400
            )
        new_name = (
            new_mcp.get("npm_package") or new_mcp.get("pypi_package") or "unknown"
        )
        if new_name != row.package_name:
            raise AppError(
                "MCP_PACKAGE_MISMATCH",
                "The updated manifest is for a different package; submit it as a "
                "new submission instead.",
                400,
            )
        manifest = body.manifest

    # Server-side re-verification is authoritative (same as initial submit).
    server_verification = await verify_registry(manifest)
    server_verification = await _attach_gate_result(
        server_verification, manifest, report, session, publisher_id=row.publisher_id
    )

    row.manifest_raw = manifest
    row.verification_report = report
    row.server_verification = server_verification
    row.status = derive_status(server_verification, report)
    await session.flush()
    await session.commit()

    logger.info(
        "MCP submission %s report updated by publisher %s: %s (server=%s)",
        row.id,
        user.publisher.slug,
        row.status,
        server_verification.get("server_status"),
    )

    updated = report
    return MaintainerSubmissionStatus(
        id=str(row.id),
        package_name=row.package_name,
        package_registry=row.package_registry,
        package_version=row.package_version,
        source_repo=row.source_repo,
        status=row.status,
        report_status=updated.get("status"),
        report_summary=updated.get("summary"),
        actions=updated.get("actions"),
        server_verification=row.server_verification,
        maintainer_feedback=row.maintainer_feedback,
        reviewed_at=row.reviewed_at.isoformat() if row.reviewed_at else None,
        published_package_id=str(row.published_package_id)
        if row.published_package_id
        else None,
        created_at=row.created_at.isoformat() if row.created_at else "",
    )


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------


class SubmissionSummary(BaseModel):
    id: str
    package_name: str
    package_registry: str
    package_version: str | None
    source_repo: str | None
    status: str
    report_status: str | None
    server_status: str | None
    report_summary: str | None
    actions_high: int
    actions_medium: int
    tools_count: int
    created_at: str
    # Slice 2a advisory gate result (from server_verification.gate_result JSONB).
    # auto_publish_eligible is always False today (ownership + smoke gates are
    # not built); objective_blockers are the real blockers excluding those.
    auto_publish_eligible: bool | None = None
    objective_blockers: list[str] = []
    future_blockers: list[str] = []


class SubmissionListResponse(BaseModel):
    submissions: list[SubmissionSummary]
    total: int


class SubmissionDetail(BaseModel):
    id: str
    package_name: str
    package_registry: str
    package_version: str | None
    source_repo: str | None
    status: str
    manifest: dict
    verification_report: dict
    server_verification: dict | None
    ownership: dict
    reviewer_notes: str | None
    maintainer_feedback: str | None
    published_package_id: str | None
    created_at: str
    updated_at: str


class ReviewRequest(BaseModel):
    status: str = Field(
        ..., description="New status: approved, rejected, needs_changes"
    )
    notes: str | None = Field(None, description="Internal reviewer notes (admin-only)")
    maintainer_feedback: str | None = Field(
        None, description="Message visible to the submitting maintainer"
    )


class ReviewResponse(BaseModel):
    id: str
    status: str
    message: str


ADMIN_VALID_STATUSES = set(mcp_status.ADMIN_SETTABLE) | {mcp_status.ACTION_REQUIRED}


# ---------------------------------------------------------------------------
# Publish-challenge ownership (Slice 2b-2): prove package control by publishing
# a version whose keywords carry a server-issued token. Strong, automated, and
# self-contained (npm + PyPI, no OAuth). This produces STRONG ownership evidence
# but activates NO auto-publish — sandbox_smoke is still a future blocker and MCP
# stays review-gated.
# ---------------------------------------------------------------------------


class ChallengeRequest(BaseModel):
    registry: str = Field(..., description="npm | pypi")
    package_name: str = Field(..., min_length=1, max_length=200)


class ChallengeIssueResponse(BaseModel):
    token: str  # shown ONCE — only stored as a hash server-side
    keyword: str
    registry: str
    package_name: str
    expires_at: str
    instructions: str


class ChallengeVerifyResponse(BaseModel):
    verified: bool
    status: str
    message: str
    version: str | None = None


async def _find_claim(session, publisher_id, registry, norm):
    return (
        (
            await session.execute(
                select(PublisherPackageClaim)
                .where(
                    PublisherPackageClaim.publisher_id == publisher_id,
                    PublisherPackageClaim.registry == registry,
                    PublisherPackageClaim.package_name_normalized == norm,
                )
                .order_by(PublisherPackageClaim.verified_at.desc())
            )
        )
        .scalars()
        .first()
    )


@router.post(
    "/ownership/challenge",
    response_model=ChallengeIssueResponse,
    dependencies=[Depends(rate_limit(10, 60))],
)
async def issue_ownership_challenge(
    body: ChallengeRequest,
    user: User = Depends(require_publisher),
    session: AsyncSession = Depends(get_session),
):
    """Issue a publish-challenge: returns a one-time token + the keyword to add to
    a newly published package version. Only the hash is stored."""
    from app.mcp import ownership as own

    registry = body.registry.strip().lower()
    if registry not in ("npm", "pypi"):
        raise AppError("MCP_INVALID_REGISTRY", "registry must be 'npm' or 'pypi'", 400)
    name = body.package_name.strip()
    norm = normalize_package_name(registry, name)

    token = own.new_challenge_token()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=own.CHALLENGE_PENDING_TTL_DAYS)
    evidence = {"basis": "publish_challenge", "issued_at": now.isoformat()}

    claim = await _find_claim(session, user.publisher.id, registry, norm)
    if claim is not None:
        claim.method = "publish_challenge"
        claim.strength = "strong"
        claim.status = own.CHALLENGE_STATUS_PENDING
        claim.challenge_token_hash = own.hash_token(token)
        claim.verified_at = None
        claim.expires_at = expires
        claim.evidence = evidence
    else:
        session.add(
            PublisherPackageClaim(
                id=uuid4(),
                publisher_id=user.publisher.id,
                registry=registry,
                package_name=name,
                package_name_normalized=norm,
                method="publish_challenge",
                strength="strong",
                status=own.CHALLENGE_STATUS_PENDING,
                challenge_token_hash=own.hash_token(token),
                evidence=evidence,
                expires_at=expires,
            )
        )
    await session.commit()

    return ChallengeIssueResponse(
        token=token,
        keyword=own.challenge_keyword(token),
        registry=registry,
        package_name=name,
        expires_at=expires.isoformat(),
        instructions=(
            f"Add the keyword '{own.challenge_keyword(token)}' to your package's "
            "keywords, publish a new version, then call verify. The token is shown "
            "only once and is stored only as a hash."
        ),
    )


@router.post(
    "/ownership/challenge/verify",
    response_model=ChallengeVerifyResponse,
    dependencies=[Depends(rate_limit(10, 60))],
)
async def verify_ownership_challenge(
    body: ChallengeRequest,
    user: User = Depends(require_publisher),
    session: AsyncSession = Depends(get_session),
):
    """Verify a pending publish-challenge by checking the latest published version's
    keywords for the token. On success the claim becomes STRONG + verified."""
    from app.mcp import ownership as own
    from app.mcp.registry_verify import RegistryUnavailable, _fetch_npm, _fetch_pypi

    registry = body.registry.strip().lower()
    if registry not in ("npm", "pypi"):
        raise AppError("MCP_INVALID_REGISTRY", "registry must be 'npm' or 'pypi'", 400)
    name = body.package_name.strip()
    norm = normalize_package_name(registry, name)

    claim = await _find_claim(session, user.publisher.id, registry, norm)
    now = datetime.now(timezone.utc)
    if (
        claim is None
        or claim.method != "publish_challenge"
        or not claim.challenge_token_hash
    ):
        raise AppError(
            "MCP_NO_CHALLENGE",
            "No publish-challenge to verify — issue one first.",
            404,
        )
    if claim.status != own.CHALLENGE_STATUS_PENDING:
        return ChallengeVerifyResponse(
            verified=claim.status == "verified",
            status=claim.status,
            message=f"Challenge is already '{claim.status}'.",
        )
    if claim.expires_at and claim.expires_at <= now:
        raise AppError(
            "MCP_CHALLENGE_EXPIRED",
            "The challenge has expired — issue a new one.",
            400,
        )

    try:
        data = await _fetch_npm(name) if registry == "npm" else await _fetch_pypi(name)
    except RegistryUnavailable:
        raise AppError(
            "MCP_REGISTRY_UNAVAILABLE",
            "The registry is temporarily unavailable — try verify again shortly.",
            503,
        )
    if data is None:
        raise AppError(
            "MCP_PACKAGE_NOT_FOUND",
            f"Package '{name}' not found on {registry}.",
            404,
        )

    match = own.find_challenge_match(registry, data, claim.challenge_token_hash)
    if not match["found"]:
        return ChallengeVerifyResponse(
            verified=False,
            status=own.CHALLENGE_STATUS_PENDING,
            message=(
                "Token not found in the latest published version's keywords yet. "
                "Publish a version with the keyword, then verify again."
            ),
        )

    claim.status = "verified"
    claim.verified_at = now
    claim.verified_by_id = user.id
    claim.expires_at = now + timedelta(days=OWNERSHIP_CLAIM_TTL_DAYS)
    claim.evidence = {
        "basis": "publish_challenge",
        "verified_via": "published_version_keyword",
        "version": match["version"],
    }
    await session.commit()

    return ChallengeVerifyResponse(
        verified=True,
        status="verified",
        message="Ownership verified via a published version — strong evidence.",
        version=match["version"],
    )


@admin_router.get(
    "/submissions",
    response_model=SubmissionListResponse,
    dependencies=[Depends(rate_limit(30, 60))],
)
async def list_submissions(
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """List MCP submissions for admin review."""
    query = select(McpSubmission).order_by(McpSubmission.created_at.desc())
    count_query = select(func.count(McpSubmission.id))

    if status:
        query = query.where(McpSubmission.status == status)
        count_query = count_query.where(McpSubmission.status == status)

    total = (await session.execute(count_query)).scalar() or 0
    query = query.offset((page - 1) * per_page).limit(per_page)
    rows = (await session.execute(query)).scalars().all()

    submissions = []
    for s in rows:
        report = s.verification_report or {}
        actions = report.get("actions", [])
        gate = (s.server_verification or {}).get("gate_result") or {}
        submissions.append(
            SubmissionSummary(
                id=str(s.id),
                package_name=s.package_name,
                package_registry=s.package_registry,
                package_version=s.package_version,
                source_repo=s.source_repo,
                status=s.status,
                report_status=report.get("status"),
                server_status=(s.server_verification or {}).get("server_status"),
                report_summary=report.get("summary"),
                actions_high=sum(1 for a in actions if a.get("severity") == "high"),
                actions_medium=sum(1 for a in actions if a.get("severity") == "medium"),
                tools_count=len(report.get("tools_snapshot", [])),
                created_at=s.created_at.isoformat() if s.created_at else "",
                auto_publish_eligible=gate.get("auto_publish_eligible"),
                objective_blockers=gate.get("objective_blockers") or [],
                future_blockers=gate.get("future_blockers") or [],
            )
        )

    return SubmissionListResponse(submissions=submissions, total=total)


@admin_router.get(
    "/submissions/{submission_id}",
    response_model=SubmissionDetail,
    dependencies=[Depends(rate_limit(30, 60))],
)
async def get_submission(
    submission_id: UUID,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Get full submission detail including manifest and report."""
    row = (
        await session.execute(
            select(McpSubmission).where(McpSubmission.id == submission_id)
        )
    ).scalar_one_or_none()

    if not row:
        raise AppError("MCP_SUBMISSION_NOT_FOUND", "Submission not found", 404)

    ownership = _ownership_view(await _latest_claim(session, row))

    return SubmissionDetail(
        id=str(row.id),
        package_name=row.package_name,
        package_registry=row.package_registry,
        package_version=row.package_version,
        source_repo=row.source_repo,
        status=row.status,
        manifest=row.manifest_raw,
        verification_report=row.verification_report,
        server_verification=row.server_verification,
        ownership=ownership,
        reviewer_notes=row.reviewer_notes,
        maintainer_feedback=row.maintainer_feedback,
        published_package_id=str(row.published_package_id)
        if row.published_package_id
        else None,
        created_at=row.created_at.isoformat() if row.created_at else "",
        updated_at=row.updated_at.isoformat() if row.updated_at else "",
    )


@admin_router.put(
    "/submissions/{submission_id}/review",
    response_model=ReviewResponse,
    dependencies=[Depends(rate_limit(10, 60))],
)
async def review_submission(
    submission_id: UUID,
    body: ReviewRequest,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Set review status on a submission."""
    if body.status not in ADMIN_VALID_STATUSES:
        raise AppError(
            "MCP_INVALID_STATUS",
            f"Status must be one of: {', '.join(sorted(ADMIN_VALID_STATUSES))}",
            400,
        )

    row = (
        await session.execute(
            select(McpSubmission).where(McpSubmission.id == submission_id)
        )
    ).scalar_one_or_none()

    if not row:
        raise AppError("MCP_SUBMISSION_NOT_FOUND", "Submission not found", 404)

    row.status = body.status
    row.reviewer_notes = body.notes
    row.maintainer_feedback = body.maintainer_feedback
    row.reviewed_by_id = user.id
    row.reviewed_at = datetime.now(timezone.utc)
    row.updated_at = datetime.now(timezone.utc)

    await session.flush()
    await session.commit()

    logger.info(
        "MCP submission %s reviewed by %s: %s", submission_id, user.email, body.status
    )

    return ReviewResponse(
        id=str(row.id),
        status=row.status,
        message=f"Submission {row.package_name} marked as {body.status}.",
    )


# ---------------------------------------------------------------------------
# Catalog publication
# ---------------------------------------------------------------------------


class PublishResponse(BaseModel):
    submission_id: str
    package_slug: str
    package_id: str
    message: str


def _verify_publish_gate(
    submission: McpSubmission, ownership_claim: PublisherPackageClaim | None
) -> list[str]:
    """Validate all publish gate rules. Returns list of blocking reasons.

    Three independent axes, never substituted for one another:
    - registry facts (package_exists/version/repo_consistency) from
      ``server_verification`` — authoritative, server-derived;
    - package_control from ``ownership_claim`` (publisher_package_claims) — the
      ONLY source for ownership. repo_consistency must never satisfy it;
    - runtime attestation from the client ``verification_report`` — advisory.
    """
    blocks: list[str] = []

    if submission.status != "approved":
        blocks.append(f"Submission status is '{submission.status}', must be 'approved'")

    if submission.published_package_id is not None:
        blocks.append("Already published")

    manifest = submission.manifest_raw or {}
    if manifest.get("runtime") != "mcp":
        blocks.append("Manifest runtime is not 'mcp'")
    if not isinstance(manifest.get("mcp_server"), dict):
        blocks.append("Manifest missing mcp_server block")

    # --- Server-verified registry facts (authoritative) ---
    sv = submission.server_verification or {}
    if not sv:
        blocks.append("No server verification on record — run re-verify first")
    else:
        sstatus = sv.get("server_status")
        if sstatus == "mismatch":
            reasons = (
                "; ".join(sv.get("errors") or []) or "registry contradicts manifest"
            )
            blocks.append(f"Server verification status is 'mismatch': {reasons}")
        elif sstatus == "unavailable":
            blocks.append(
                "Server verification incomplete (registry was unavailable) — re-verify before publishing"
            )
        if not sv.get("package_exists"):
            blocks.append("Server could not confirm the package exists on the registry")
        if not sv.get("resolved_version"):
            blocks.append("Server could not resolve a published version")
        # repo_consistency: 'mismatch' blocks; 'indeterminate' is allowed (admin warning, not a hard block)
        if sv.get("repo_consistency") == "mismatch":
            blocks.append(
                "source_repo does not match registry metadata (repo_consistency mismatch)"
            )

    # --- Maintainer-attested protocol test (not server-verifiable without execution) ---
    report = submission.verification_report or {}
    if report.get("status") != "TESTED":
        blocks.append(
            f"Maintainer-attested status is '{report.get('status')}', must be 'TESTED'"
        )

    high_actions = [a for a in report.get("actions", []) if a.get("severity") == "high"]
    if high_actions:
        codes = [a.get("code", "?") for a in high_actions]
        blocks.append(
            f"Has {len(high_actions)} high-severity action(s): {', '.join(codes)}"
        )

    # --- Package control (ownership) — a SEPARATE axis. repo_consistency must
    # NEVER satisfy this; only a verified, non-expired publisher_package_claim does. ---
    now = datetime.now(timezone.utc)
    if ownership_claim is None:
        blocks.append(
            "No ownership claim — package control is not proven (repo_consistency does not count)"
        )
    elif ownership_claim.status == "revoked":
        blocks.append("Ownership claim was revoked")
    elif ownership_claim.status != "verified":
        blocks.append(f"Ownership claim is '{ownership_claim.status}', not verified")
    elif ownership_claim.expires_at is not None and ownership_claim.expires_at <= now:
        blocks.append("Ownership claim has expired — re-verify package control")

    return blocks


@admin_router.post(
    "/submissions/{submission_id}/publish",
    response_model=PublishResponse,
    dependencies=[Depends(rate_limit(5, 60))],
)
async def publish_submission(
    submission_id: UUID,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Publish an approved MCP submission to the catalog via publish_package()."""
    from app.packages.models import Package, PackageVersion
    from app.packages.service import publish_package

    row = (
        await session.execute(
            select(McpSubmission).where(McpSubmission.id == submission_id)
        )
    ).scalar_one_or_none()

    if not row:
        raise AppError("MCP_SUBMISSION_NOT_FOUND", "Submission not found", 404)

    ownership_claim = await _latest_claim(session, row)
    blocks = _verify_publish_gate(row, ownership_claim)
    if blocks:
        raise AppError("MCP_PUBLISH_BLOCKED", "; ".join(blocks), 400)

    # Publish on a copy so the original (audit) submission keeps the raw command.
    # If the server resolved a pinned npm command, the catalog entry uses it for
    # reproducibility (server_verification.pinned_command, npm/npx only).
    manifest = copy.deepcopy(row.manifest_raw)
    pinned_cmd = (row.server_verification or {}).get("pinned_command")
    if pinned_cmd and isinstance(manifest.get("mcp_server"), dict):
        manifest["mcp_server"]["command"] = pinned_cmd
    slug = manifest.get("package_id", row.package_name)
    version = manifest.get("version", row.package_version or "0.1.0")

    # Check if package+version already exists in catalog
    existing = (
        await session.execute(
            select(PackageVersion.id)
            .join(Package, Package.id == PackageVersion.package_id)
            .where(Package.slug == slug, PackageVersion.version_number == version)
        )
    ).scalar_one_or_none()

    if existing:
        raise AppError(
            "MCP_ALREADY_IN_CATALOG",
            f"{slug}@{version} already exists in the catalog",
            409,
        )

    try:
        pkg, pv, warnings = await publish_package(
            manifest=manifest,
            publisher_id=row.publisher_id,
            session=session,
            artifact_bytes=None,
        )
    except AppError:
        raise
    except Exception as e:
        raise AppError("MCP_PUBLISH_FAILED", f"Publish failed: {e}", 500)

    row.published_package_id = pkg.id
    # Converged terminal state: 'published' is now a real status (was implicit in
    # published_package_id). Only this admin path sets it — never derive_status.
    row.status = mcp_status.PUBLISHED
    row.updated_at = datetime.now(timezone.utc)
    await session.commit()

    logger.info(
        "MCP submission %s published as %s by admin %s",
        submission_id,
        pkg.slug,
        user.email,
    )

    return PublishResponse(
        submission_id=str(row.id),
        package_slug=pkg.slug,
        package_id=str(pkg.id),
        message=f"{pkg.slug}@{pv.version_number} published to catalog.",
    )


# ---------------------------------------------------------------------------
# Re-verification (recover from 'unavailable', refresh before approving)
# ---------------------------------------------------------------------------


class ReverifyResponse(BaseModel):
    id: str
    server_status: str | None
    status: str
    message: str


@admin_router.post(
    "/submissions/{submission_id}/reverify",
    response_model=ReverifyResponse,
    dependencies=[Depends(rate_limit(10, 60))],
)
async def reverify_submission(
    submission_id: UUID,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Re-run server-side registry verification for a submission.

    Recovers submissions stuck on ``unavailable`` (registry was down at submit
    time) and lets an admin refresh the registry facts before approving. Does not
    touch already-published or admin-decided submissions beyond refreshing facts;
    re-derives status only while still in the open queue.
    """
    row = (
        await session.execute(
            select(McpSubmission).where(McpSubmission.id == submission_id)
        )
    ).scalar_one_or_none()

    if not row:
        raise AppError("MCP_SUBMISSION_NOT_FOUND", "Submission not found", 404)

    old_sv = row.server_verification or {}
    sv = await verify_registry(row.manifest_raw or {})
    # 2c-4b: carry the existing smoke forward so reverify doesn't wipe it — the
    # freshness check decides whether a recheck is needed (no needless re-run).
    if old_sv.get("smoke"):
        sv["smoke"] = old_sv["smoke"]
    if old_sv.get("smoke_running"):
        sv["smoke_running"] = old_sv["smoke_running"]
    sv = await _attach_gate_result(
        sv,
        row.manifest_raw or {},
        row.verification_report or {},
        session,
        publisher_id=row.publisher_id,
    )
    row.server_verification = sv
    if sv.get("resolved_version"):
        row.package_version = sv["resolved_version"]
    if row.status in mcp_status.REVERIFY_SOURCE:
        row.status = derive_status(sv, row.verification_report or {})
    row.updated_at = datetime.now(timezone.utc)
    await session.commit()

    # 2c-4b: schedule a smoke recheck if enabled + eligible + not already fresh.
    # Inert no-op unless MCP_SMOKE_MODE=container. Never blocks the response.
    from app.mcp.smoke_executor import maybe_schedule_smoke

    maybe_schedule_smoke(background_tasks, row.id, row.manifest_raw or {}, sv)

    logger.info(
        "MCP submission %s re-verified by %s: server=%s status=%s",
        submission_id,
        user.email,
        sv.get("server_status"),
        row.status,
    )

    return ReverifyResponse(
        id=str(row.id),
        server_status=sv.get("server_status"),
        status=row.status,
        message=f"Re-verified: server status '{sv.get('server_status')}', submission now '{row.status}'.",
    )


# ---------------------------------------------------------------------------
# Ownership (package_control) — manual admin verification (Step 1)
# ---------------------------------------------------------------------------


class VerifyOwnershipRequest(BaseModel):
    reason: str = Field(
        ..., min_length=1, description="Audit reason for the manual ownership decision"
    )


class VerifyOwnershipResponse(BaseModel):
    claim_id: str
    status: str
    message: str


@admin_router.post(
    "/submissions/{submission_id}/verify-ownership",
    response_model=VerifyOwnershipResponse,
    dependencies=[Depends(rate_limit(10, 60))],
)
async def verify_ownership_manual(
    submission_id: UUID,
    body: VerifyOwnershipRequest,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Admin explicitly attests package ownership (method=manual_admin).

    This satisfies the ownership (package_control) dimension of the publish gate
    for this publisher+package. It is an explicit, audited decision — NOT derived
    from repo_consistency. Creates a time-bounded verified claim.
    """
    row = (
        await session.execute(
            select(McpSubmission).where(McpSubmission.id == submission_id)
        )
    ).scalar_one_or_none()
    if not row:
        raise AppError("MCP_SUBMISSION_NOT_FOUND", "Submission not found", 404)

    now = datetime.now(timezone.utc)
    claim = PublisherPackageClaim(
        id=uuid4(),
        publisher_id=row.publisher_id,
        registry=row.package_registry,
        package_name=row.package_name,
        package_name_normalized=normalize_package_name(
            row.package_registry, row.package_name
        ),
        method="manual_admin",
        strength="manual",
        status="verified",
        evidence={
            "basis": "manual_admin",
            "submission_id": str(submission_id),
            "reason": body.reason,
        },
        verified_at=now,
        verified_by_id=user.id,
        expires_at=now + timedelta(days=OWNERSHIP_CLAIM_TTL_DAYS),
    )
    session.add(claim)
    await session.flush()
    await session.commit()

    logger.info(
        "MCP ownership manually verified: %s/%s by admin %s (claim %s)",
        row.package_registry,
        row.package_name,
        user.email,
        claim.id,
    )

    return VerifyOwnershipResponse(
        claim_id=str(claim.id),
        status="verified",
        message=f"Ownership of {row.package_name} marked manually verified (audited).",
    )


class RevokeOwnershipRequest(BaseModel):
    reason: str = Field(
        ..., min_length=1, description="Audit reason for revoking the claim"
    )


class RevokeOwnershipResponse(BaseModel):
    claim_id: str
    status: str
    message: str


@admin_router.post(
    "/claims/{claim_id}/revoke",
    response_model=RevokeOwnershipResponse,
    dependencies=[Depends(rate_limit(10, 60))],
)
async def revoke_ownership(
    claim_id: UUID,
    body: RevokeOwnershipRequest,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Revoke a verified ownership claim. Package control is not permanent
    (transfers, lost access, admin error) — this is the auditable counterpart to
    grant. A revoked claim no longer satisfies the publish gate. Scoped to the
    claim (which is per publisher+package), not a single submission.
    """
    claim = (
        await session.execute(
            select(PublisherPackageClaim).where(PublisherPackageClaim.id == claim_id)
        )
    ).scalar_one_or_none()
    if not claim:
        raise AppError("MCP_CLAIM_NOT_FOUND", "Ownership claim not found", 404)
    if claim.status != "verified":
        raise AppError(
            "MCP_CLAIM_NOT_REVOCABLE",
            f"Only verified claims can be revoked (status: {claim.status})",
            400,
        )

    now = datetime.now(timezone.utc)
    evidence = dict(claim.evidence or {})
    evidence["revoke_reason"] = body.reason
    evidence["revoked_by_id"] = str(user.id)
    evidence["revoked_at"] = now.isoformat()
    claim.evidence = evidence  # reassign so SQLAlchemy detects the JSONB change
    claim.status = "revoked"
    claim.updated_at = now
    await session.flush()
    await session.commit()

    logger.info(
        "MCP ownership claim %s revoked by admin %s (%s/%s)",
        claim.id,
        user.email,
        claim.registry,
        claim.package_name,
    )

    return RevokeOwnershipResponse(
        claim_id=str(claim.id),
        status="revoked",
        message=f"Ownership claim for {claim.package_name} revoked (audited).",
    )
