"""Verification pipeline orchestrator — runs async after publish.

Follows the same background-task pattern as trust/scanner.py.
Supports multiple verification runs per version (append-only history).
"""

from __future__ import annotations

import asyncio
import logging
import platform
import sys
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import and_, func, select

from app.config import settings
from app.database import async_session_factory

logger = logging.getLogger(__name__)

RUNNER_VERSION = "2.0.0"

# --- Quarantine auto-clear gating ------------------------------------------
#
# Auto-clear is ONLY valid for quarantine reasons the publish/verification
# pipeline set automatically. Admin-imposed quarantine, security-scan
# quarantine, and any reason not listed here must never be cleared by a
# passing verification run. Canonicalized to snake_case; the matching
# prefix `Auto-quarantined: verification failed` covers the legacy reason
# strings with extra detail in parentheses.  (P1-L4)
AUTO_CLEARABLE_REASONS: frozenset[str] = frozenset({
    "Auto-quarantined: verification failed",
    "new_publisher_review",
})
_AUTO_CLEAR_PREFIX = "Auto-quarantined: verification failed"

# Triggers that MUST NOT auto-clear quarantine — e.g. an owner-initiated
# re-verify must not undo a quarantine the admin imposed. (P1-V1)
_NON_AUTO_CLEARING_TRIGGERS: frozenset[str] = frozenset({
    "owner_request",
    "admin_reverify",
})

# --- Platform error detection -------------------------------------------------
# Errors matching these patterns are platform-side issues (misconfiguration,
# missing infra) — NOT the publisher's fault. When detected:
#   1. Package status is NOT changed
#   2. Admin receives an alert
#   3. Publisher sees nothing
_PLATFORM_ERROR_PATTERNS: tuple[str, ...] = (
    "Enforced sandbox unavailable",
    "no container runtime",
    "pull access denied",
    "Unable to find image",
    "Container execution error",
    "docker: Error response from daemon",
    "Internal verification error",
)


def _is_platform_error(step_results: dict) -> bool:
    """Return True if the verification failure is caused by platform misconfiguration."""
    for field in ("install_log", "import_log", "smoke_log", "error_summary"):
        text = step_results.get(field) or ""
        for pattern in _PLATFORM_ERROR_PATTERNS:
            if pattern in text:
                return True
    return False


async def _has_open_scanner_findings(session, version_id: UUID) -> bool:
    """Return True if the scanner recorded any unresolved medium/high/critical
    finding for this version. Used as a secondary gate on auto-clear so the
    publish-trust pipeline cannot promote a package that the scanner flagged
    as dangerous (P0-02).
    """
    from app.packages.models import SecurityFinding

    stmt = (
        select(func.count(SecurityFinding.id))
        .where(
            SecurityFinding.package_version_id == version_id,
            SecurityFinding.is_resolved.is_(False),
            SecurityFinding.severity.in_(("medium", "high", "critical")),
        )
    )
    count = (await session.execute(stmt)).scalar() or 0
    return count > 0


async def _log_candidate_verification(package_id: UUID) -> None:
    """If this package was published by an invited candidate, log verification_passed."""
    from app.invites.models import ImportCandidate, CandidateEvent
    from app.invites.service import log_event

    async with async_session_factory() as session:
        # Find candidate by published_package_id
        result = await session.execute(
            select(ImportCandidate.id).where(
                ImportCandidate.published_package_id == package_id
            )
        )
        candidate_id = result.scalar_one_or_none()
        if not candidate_id:
            return

        # Idempotency: check if verification_passed already logged for this package
        existing = await session.execute(
            select(CandidateEvent.id).where(
                CandidateEvent.candidate_id == candidate_id,
                CandidateEvent.event_type == "verification_passed",
                CandidateEvent.metadata_["package_id"].astext == str(package_id),
            ).limit(1)
        )
        if existing.scalar_one_or_none() is not None:
            return

        await log_event(session, candidate_id, "verification_passed", {"package_id": str(package_id)})
        await session.commit()

# Concurrency limiter — prevents VPS overload on parallel publishes
_verification_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _verification_semaphore
    if _verification_semaphore is None:
        _verification_semaphore = asyncio.Semaphore(settings.VERIFICATION_MAX_CONCURRENT)
    return _verification_semaphore


def _run_verification_sync(
    artifact_bytes: bytes,
    tools: list[dict],
) -> dict:
    """Run all verification steps synchronously (called in thread pool).

    Returns a dict with all step results using status strings, not booleans.
    """
    from app.verification.sandbox import IsolationLevel, VerificationSandbox
    from app.verification.steps import step_import, step_install, step_smoke, step_tests, run_stability_check
    from app.verification.smoke_context import (
        build_smoke_context, classify_credential_boundary, REASON_VERDICTS,
    )

    sandbox = VerificationSandbox()
    result = {
        "install_status": None,
        "import_status": None,
        "smoke_status": None,
        "tests_status": None,
        "install_log": "",
        "import_log": "",
        "smoke_log": "",
        "tests_log": "",
        "install_duration_ms": None,
        "import_duration_ms": None,
        "smoke_duration_ms": None,
        "tests_duration_ms": None,
        "error_summary": None,
        "warnings_count": 0,
        "warnings_summary": None,
        "smoke_reason": None,
        # Phase 5+6 new fields
        "installer": "pip",
        "verification_mode": "real",
        "contract_details": None,
        "smoke_confidence": None,
        # Isolation levels per step — determined dynamically from sandbox capabilities
        "isolation": None,  # populated after sandbox creation
    }

    try:
        # Extract artifact
        if not sandbox.extract_artifact(artifact_bytes):
            result["install_status"] = "error"
            result["install_log"] = "Failed to extract artifact"
            result["error_summary"] = "Artifact extraction failed"
            return result

        # Determine isolation levels based on actual sandbox capabilities
        result["isolation"] = {
            "install": sandbox.get_isolation_level("install"),
            "import": sandbox.get_isolation_level("import"),
            "smoke": sandbox.get_isolation_level("smoke"),
            "tests": sandbox.get_isolation_level("tests"),
            "overall": sandbox.get_isolation_level("import"),  # import is the primary enforced path
        }

        # Step 1: Install
        t0 = time.monotonic()
        ok, log = step_install(sandbox)
        result["install_duration_ms"] = int((time.monotonic() - t0) * 1000)
        result["install_status"] = "passed" if ok else "failed"
        result["install_log"] = log
        result["installer"] = sandbox.installer
        if not ok:
            result["error_summary"] = "Package installation failed"
            return result

        # Step 2: Import
        t0 = time.monotonic()
        ok, log = step_import(sandbox, tools)
        result["import_duration_ms"] = int((time.monotonic() - t0) * 1000)
        result["import_status"] = "passed" if ok else "failed"
        result["import_log"] = log
        if not ok:
            result["error_summary"] = "Tool entrypoint import verification failed"
            return result

        # Step 3: Smoke test (returns 3-tuple: status, log, reason)
        t0 = time.monotonic()
        smoke_status, log, smoke_reason = step_smoke(sandbox, tools)
        result["smoke_duration_ms"] = int((time.monotonic() - t0) * 1000)
        result["smoke_status"] = smoke_status
        result["smoke_log"] = log
        result["smoke_reason"] = smoke_reason

        # Phase 7A: Determine smoke confidence for credential boundary
        if smoke_reason in ("credential_boundary_reached", "needs_credentials"):
            # Parse the smoke log to find the confidence
            import json as _json
            for line in log.splitlines():
                try:
                    entry = _json.loads(line)
                    if isinstance(entry, dict) and entry.get("error_type"):
                        # Re-classify to get confidence
                        ctx_tool = tools[0] if tools else {}
                        ctx = build_smoke_context(ctx_tool)
                        _, confidence = classify_credential_boundary(
                            entry.get("error_type", ""),
                            (entry.get("message") or "").lower(),
                            ctx,
                        )
                        if confidence:
                            result["smoke_confidence"] = confidence
                            break
                except (_json.JSONDecodeError, Exception):
                    continue

        # Phase 6E: Determine verification_mode
        from app.config import SYSTEM_CAPABILITIES
        if smoke_reason == "missing_system_dependency":
            result["verification_mode"] = "limited"
        elif smoke_reason == "needs_binary_input":
            result["verification_mode"] = "limited"
        elif smoke_reason in ("credential_boundary_reached", "needs_credentials"):
            result["verification_mode"] = "real"  # Tool ran, just hit auth
        else:
            result["verification_mode"] = "real"

        # Step 3b: Stability check (Phase 4A) — only if smoke passed
        result["reliability"] = None
        result["determinism_score"] = None
        result["contract_valid"] = None
        result["stability_log"] = None
        result["contract_details"] = None

        if smoke_status == "passed" and tools:
            # Find first tool that passed smoke — use its candidate
            first_passed_tool = None
            for tool in tools:
                if tool.get("entrypoint") and ":" in tool["entrypoint"]:
                    first_passed_tool = tool
                    break

            if first_passed_tool:
                try:
                    from app.verification.schema_generator import generate_candidates
                    ctx = build_smoke_context(first_passed_tool)
                    module_path, func_name = first_passed_tool["entrypoint"].rsplit(":", 1)
                    candidates = generate_candidates(first_passed_tool.get("input_schema"))
                    if candidates:
                        remaining = settings.VERIFICATION_SMOKE_BUDGET_SECONDS - (
                            (result.get("smoke_duration_ms") or 0) / 1000
                        )
                        if remaining > 10:
                            reliability, determinism, contract_valid, stability_results = run_stability_check(
                                sandbox, module_path, func_name,
                                candidates[0], min(10, max(3, int(remaining / 3))),
                                ctx, n=3,
                            )
                            result["reliability"] = reliability
                            result["determinism_score"] = determinism
                            result["contract_valid"] = contract_valid
                            result["stability_log"] = stability_results

                            # Phase 6A: Contract validation
                            if stability_results:
                                last_ok = next(
                                    (r for r in reversed(stability_results) if r.get("ok")),
                                    None,
                                )
                                if last_ok:
                                    from app.verification.contract import validate_return
                                    smoke_data = {
                                        "status": "ok",
                                        "return_type": last_ok.get("type"),
                                        "return_hash": last_ok.get("hash"),
                                        "is_none": last_ok.get("is_none", True),
                                        "is_serializable": last_ok.get("is_serializable", False),
                                        "return_keys": None,
                                        "return_length": None,
                                    }
                                    contract_result = validate_return(
                                        smoke_data,
                                        first_passed_tool.get("name", ""),
                                        candidates[0],
                                    )
                                    result["contract_details"] = contract_result
                                    result["contract_valid"] = contract_result.get("valid", False)
                except Exception:
                    logger.exception("Stability check failed (non-fatal)")

        # Step 4: Tests (auto-generate if none exist)
        tests_auto_generated = False
        if not sandbox.has_tests():
            sandbox.generate_auto_tests(tools)
            tests_auto_generated = True
        if sandbox.has_tests():
            t0 = time.monotonic()
            ok, log = step_tests(sandbox)
            result["tests_duration_ms"] = int((time.monotonic() - t0) * 1000)
            result["tests_status"] = "passed" if ok else "failed"
            result["tests_log"] = log
        else:
            result["tests_status"] = "not_present"
            result["tests_log"] = "No test directory found"
        result["tests_auto_generated"] = tests_auto_generated

        # Collect warnings from all logs
        warnings = []
        for log_key in ("import_log", "smoke_log"):
            log_text = result.get(log_key, "")
            for line in log_text.splitlines():
                if line.startswith("WARN:") or line.startswith("[WARN]") or line.startswith("[INCONCLUSIVE]"):
                    warnings.append(line.strip())
        result["warnings_count"] = len(warnings)
        if warnings:
            result["warnings_summary"] = "; ".join(warnings[:10])  # Cap at 10

        # Build summary
        failures = []
        if result["install_status"] == "failed":
            failures.append("install")
        if result["import_status"] == "failed":
            failures.append("import")
        if result["smoke_status"] == "failed":
            failures.append("smoke")
        if result["tests_status"] == "failed":
            failures.append("tests")

        if failures:
            result["error_summary"] = f"Verification failed: {', '.join(failures)}"
        else:
            result["error_summary"] = None

        return result

    finally:
        sandbox.cleanup()


async def run_verification(
    version_id: UUID,
    triggered_by: str = "publish",
    admin_user_id: UUID | None = None,
) -> None:
    """Run verification pipeline on a published package version.

    Called as a background task after publish, parallel to security scan.
    Uses a semaphore to limit concurrent verifications on single-VPS.
    Creates a new VerificationResult row each run (append-only history).

    Args:
        version_id: The package version to verify.
        triggered_by: "publish", "admin_reverify", or "runner_upgrade".
        admin_user_id: If triggered_by='admin_reverify', the admin who fired
            it. Used to write an `admin_audit_logs` entry with the outcome
            once verification completes (P1-V2).
    """
    if not settings.VERIFICATION_ENABLED:
        return

    async with _get_semaphore():
        try:
            async with async_session_factory() as session:
                from app.packages.models import Capability, Package, PackageVersion
                from app.verification.models import VerificationResult

                # Lock the PackageVersion row to prevent concurrent verification runs.
                # P1-L3: the FOR UPDATE lock is deliberately released by the
                # commit that follows the "running" VR insert below. From that
                # point on, serialization is enforced by the running-VR check
                # (stale_cutoff guarded). Holding FOR UPDATE across the slow
                # verify step would pin a write row for the full timeout
                # budget (~minutes) and block unrelated pv updates.
                pv_result = await session.execute(
                    select(PackageVersion)
                    .where(PackageVersion.id == version_id)
                    .with_for_update()
                )
                pv = pv_result.scalar_one_or_none()
                if not pv:
                    return

                # Skip if no artifact (remote packages)
                if not pv.artifact_object_key:
                    pv.verification_status = "skipped"
                    await session.commit()
                    return

                # Check artifact size limit
                if pv.artifact_size_bytes and pv.artifact_size_bytes > settings.VERIFICATION_MAX_ARTIFACT_MB * 1024 * 1024:
                    pv.verification_status = "skipped"
                    await session.commit()
                    logger.info(f"Skipping verification for {version_id}: artifact too large ({pv.artifact_size_bytes} bytes)")
                    return

                # Race condition guard: check for already-running verification
                now = datetime.now(timezone.utc)
                stale_cutoff = now - timedelta(seconds=settings.VERIFICATION_TIMEOUT + 60)
                running_result = await session.execute(
                    select(VerificationResult).where(
                        and_(
                            VerificationResult.package_version_id == version_id,
                            VerificationResult.status == "running",
                            VerificationResult.started_at > stale_cutoff,
                        )
                    ).limit(1)
                )
                if running_result.scalar_one_or_none() is not None:
                    logger.info(
                        f"Skipping verification for {version_id}: another run already in progress"
                    )
                    return

                # Clean up stale "running" results that exceeded timeout + buffer
                stale_result = await session.execute(
                    select(VerificationResult).where(
                        and_(
                            VerificationResult.package_version_id == version_id,
                            VerificationResult.status == "running",
                            VerificationResult.started_at <= stale_cutoff,
                        )
                    )
                )
                for stale_vr in stale_result.scalars().all():
                    stale_vr.status = "error"
                    stale_vr.error_summary = "Orphaned: exceeded timeout without completion"
                    stale_vr.completed_at = now
                    logger.warning(f"Marked orphaned verification {stale_vr.id} as error")

                # Create a NEW result row (append-only history)
                vr = VerificationResult(package_version_id=version_id)
                vr.status = "running"
                vr.started_at = now
                vr.runner_version = RUNNER_VERSION
                vr.python_version = sys.version
                vr.runner_platform = platform.platform()
                vr.triggered_by = triggered_by
                session.add(vr)

                pv.verification_status = "running"
                await session.commit()

                # Download artifact and verify integrity
                import hashlib
                from app.shared.storage import download_artifact
                artifact_bytes = await download_artifact(pv.artifact_object_key)

                if pv.artifact_hash_sha256:
                    actual_hash = hashlib.sha256(artifact_bytes).hexdigest()
                    if actual_hash != pv.artifact_hash_sha256:
                        logger.error(
                            f"Artifact integrity check failed for {version_id}: "
                            f"expected {pv.artifact_hash_sha256}, got {actual_hash}"
                        )
                        vr.status = "error"
                        vr.error_summary = "Artifact integrity check failed: hash mismatch"
                        vr.completed_at = datetime.now(timezone.utc)
                        pv.verification_status = "error"
                        await session.commit()
                        return

                # Gather tool entrypoints from capabilities
                caps_result = await session.execute(
                    select(Capability).where(
                        Capability.package_version_id == version_id,
                        Capability.capability_type == "tool",
                    )
                )
                capabilities = caps_result.scalars().all()

                # Extract network_level from manifest permissions
                manifest = pv.manifest_raw or {}
                permissions = manifest.get("permissions", {})
                network_level = "none"
                if isinstance(permissions, dict):
                    net = permissions.get("network", {})
                    if isinstance(net, dict):
                        network_level = net.get("level", "none") or "none"

                # Build normalized tool dicts for smoke context
                tools = []
                seen_eps = set()
                for cap in capabilities:
                    ep = cap.entrypoint
                    # Derive entrypoint from package-level entrypoint if capability has none
                    if not ep and pv.entrypoint:
                        ep = f"{pv.entrypoint}:run"
                    if ep and ep not in seen_eps:
                        seen_eps.add(ep)
                        tools.append({
                            "name": cap.name,
                            "entrypoint": ep,
                            "input_schema": cap.input_schema,
                            "env_requirements": pv.env_requirements,
                            "examples": pv.examples,
                            "network_level": network_level,
                        })

                # For agent packages, also verify the agent entrypoint
                pkg_result = await session.execute(
                    select(Package).where(Package.id == pv.package_id)
                )
                pkg = pkg_result.scalar_one_or_none()
                if pkg and pkg.package_type == "agent":
                    agent_section = manifest.get("agent", {})
                    agent_ep = agent_section.get("entrypoint", "")
                    if agent_ep and ":" in agent_ep and agent_ep not in seen_eps:
                        seen_eps.add(agent_ep)
                        tools.append({
                            "name": "__agent_entrypoint__",
                            "entrypoint": agent_ep,
                            "input_schema": None,
                            "env_requirements": pv.env_requirements,
                            "examples": None,
                            "network_level": network_level,
                        })

                # Run verification in thread pool (subprocess.run blocks)
                start_time = time.monotonic()
                loop = asyncio.get_running_loop()
                step_results = await asyncio.wait_for(
                    loop.run_in_executor(
                        None, _run_verification_sync, artifact_bytes, tools
                    ),
                    timeout=settings.VERIFICATION_TIMEOUT,
                )
                duration_ms = int((time.monotonic() - start_time) * 1000)

                # --- Platform error detection ---
                # If the failure is caused by our infrastructure (missing container
                # image, broken sandbox, etc.), do NOT blame the package.
                if _is_platform_error(step_results):
                    logger.error(
                        "PLATFORM ERROR during verification of %s (trigger=%s): %s",
                        version_id, triggered_by, step_results.get("error_summary"),
                    )
                    # Record the result for debugging but do NOT change package status
                    vr.status = "error"
                    vr.completed_at = datetime.now(timezone.utc)
                    vr.duration_ms = duration_ms
                    vr.install_status = step_results["install_status"]
                    vr.import_status = step_results["import_status"]
                    vr.smoke_status = step_results.get("smoke_status")
                    vr.tests_status = step_results.get("tests_status")
                    vr.install_log = step_results.get("install_log", "")
                    vr.import_log = step_results.get("import_log", "")
                    vr.smoke_log = step_results.get("smoke_log", "")
                    vr.tests_log = step_results.get("tests_log", "")
                    vr.error_summary = f"[PLATFORM] {step_results.get('error_summary', 'Unknown platform error')}"
                    await session.commit()

                    # Alert admins immediately
                    try:
                        from app.shared.email import send_platform_error_admin_alert
                        error_log = step_results.get("import_log") or step_results.get("install_log") or ""
                        pkg_result = await session.execute(
                            select(Package).where(Package.id == pv.package_id)
                        )
                        pkg = pkg_result.scalar_one_or_none()
                        slug_for_alert = pkg.slug if pkg else str(version_id)
                        await send_platform_error_admin_alert(
                            slug_for_alert,
                            pv.version_number,
                            step_results.get("error_summary", "Unknown"),
                            error_log,
                        )
                    except Exception:
                        logger.exception("Failed to send platform error admin alert")

                    return  # EXIT — do not touch package verification_status

                # Determine final status based on install/import (the hard gatekeeper steps)
                install_status = step_results["install_status"]
                import_status = step_results["import_status"]

                if install_status in ("failed", "error") or import_status in ("failed", "error"):
                    final_status = "failed"
                elif install_status == "passed" and import_status == "passed":
                    final_status = "passed"
                else:
                    final_status = "error"

                # Update verification result
                vr.status = final_status
                vr.completed_at = datetime.now(timezone.utc)
                vr.duration_ms = duration_ms
                vr.install_status = step_results["install_status"]
                vr.import_status = step_results["import_status"]
                vr.smoke_status = step_results["smoke_status"]
                vr.tests_status = step_results["tests_status"]
                vr.install_log = step_results["install_log"]
                vr.import_log = step_results["import_log"]
                vr.smoke_log = step_results["smoke_log"]
                vr.tests_log = step_results["tests_log"]
                vr.install_duration_ms = step_results.get("install_duration_ms")
                vr.import_duration_ms = step_results.get("import_duration_ms")
                vr.smoke_duration_ms = step_results.get("smoke_duration_ms")
                vr.tests_duration_ms = step_results.get("tests_duration_ms")
                vr.error_summary = step_results["error_summary"]
                vr.warnings_count = step_results["warnings_count"]
                vr.warnings_summary = step_results["warnings_summary"]
                vr.smoke_reason = step_results.get("smoke_reason")

                # Phase 4A: Stability + Score
                vr.reliability = step_results.get("reliability")
                vr.determinism_score = step_results.get("determinism_score")
                vr.contract_valid = step_results.get("contract_valid")
                vr.stability_log = step_results.get("stability_log")
                vr.tests_auto_generated = step_results.get("tests_auto_generated")

                # Phase 5B: Environment info (includes isolation levels)
                from app.config import SYSTEM_CAPABILITIES
                vr.environment_info = {
                    "python_version": sys.version,
                    "system_capabilities": SYSTEM_CAPABILITIES,
                    "sandbox_mode": settings.VERIFICATION_SANDBOX_MODE,
                    "installer": step_results.get("installer", "pip"),
                    "uv_used": step_results.get("installer") == "uv",
                    "isolation": step_results.get("isolation"),
                }

                # Phase 6E: Verification mode
                vr.verification_mode = step_results.get("verification_mode", "real")

                # Phase 6A: Contract details
                vr.contract_details = step_results.get("contract_details")

                # Phase 7A: Smoke confidence
                vr.smoke_confidence = step_results.get("smoke_confidence")

                # Compute score (Phase 6B: full ScoreResult)
                from app.verification.scoring import compute_score_result
                score_result = compute_score_result(vr)
                vr.verification_score = score_result.score
                vr.verification_tier = score_result.tier
                vr.confidence = score_result.confidence
                vr.score_breakdown = score_result.to_dict()

                await session.flush()

                # Update denormalized fields on PackageVersion
                pv.verification_status = final_status
                pv.latest_verification_result_id = vr.id
                pv.verification_run_count = (pv.verification_run_count or 0) + 1
                pv.last_verified_at = datetime.now(timezone.utc)
                pv.verification_score = score_result.score

                # P1-V4: Retroactive tier updates are downgrade-only.
                # A fresh `publish` run establishes the canonical tier. A re-
                # verify (admin_reverify, runner_upgrade, scheduled, owner_request)
                # can only REDUCE the tier — never promote, because the
                # re-verify environment may differ from the original publish
                # in ways the publisher didn't consent to.
                from app.verification.scoring import TIER_ORDER
                current_tier = pv.verification_tier
                new_tier = score_result.tier
                if triggered_by == "publish" or current_tier is None:
                    pv.verification_tier = new_tier
                else:
                    cur_rank = TIER_ORDER.get(current_tier, 0)
                    new_rank = TIER_ORDER.get(new_tier, 0)
                    if new_rank < cur_rank:
                        logger.info(
                            "Retroactive tier downgrade for %s: %s -> %s (trigger=%s)",
                            version_id, current_tier, new_tier, triggered_by,
                        )
                        pv.verification_tier = new_tier
                    else:
                        logger.info(
                            "Retroactive tier change blocked for %s: %s -> %s "
                            "is not a downgrade (trigger=%s) — keeping %s",
                            version_id, current_tier, new_tier, triggered_by, current_tier,
                        )

                # Auto-quarantine on install/import failure (only for publish, not admin re-verify)
                if final_status == "failed" and pv.quarantine_status == "none" and triggered_by == "publish":
                    pv.quarantine_status = "quarantined"
                    pv.quarantined_at = datetime.now(timezone.utc)
                    pv.quarantine_reason = (
                        f"Auto-quarantined: verification failed ({step_results['error_summary']})"
                    )
                    logger.warning(
                        f"Auto-quarantined {version_id}: {step_results['error_summary']}"
                    )

                # Auto-clear quarantine on verification pass — but ONLY for
                # verification-related quarantine reasons (auto-quarantine from
                # failed verification or new-publisher review).  Admin-imposed
                # quarantine (security concerns, policy violations, etc.) and
                # security-scan quarantine must NOT be bypassed.
                #
                # Additional gates (Sprint A):
                #   P0-02: refuse to clear if the scanner recorded open
                #          medium/high/critical findings for this version.
                #   P1-V1: refuse to clear for owner/admin-initiated
                #          re-verifies — only a fresh `publish` trigger is
                #          allowed to promote a quarantined version.
                if final_status == "passed" and pv.quarantine_status == "quarantined":
                    reason = pv.quarantine_reason or ""
                    is_auto_clearable = (
                        reason in AUTO_CLEARABLE_REASONS
                        or reason.startswith(f"{_AUTO_CLEAR_PREFIX} (")
                    )
                    if is_auto_clearable and triggered_by in _NON_AUTO_CLEARING_TRIGGERS:
                        logger.info(
                            "Quarantine NOT auto-cleared for %s: "
                            "triggered_by=%s is not allowed to auto-clear",
                            version_id, triggered_by,
                        )
                        is_auto_clearable = False
                    if is_auto_clearable and await _has_open_scanner_findings(session, version_id):
                        logger.warning(
                            "Quarantine NOT auto-cleared for %s: "
                            "open scanner findings present (P0-02 gate)",
                            version_id,
                        )
                        is_auto_clearable = False
                    if is_auto_clearable:
                        pv.quarantine_status = "cleared"
                        pv.quarantine_reason = None
                        logger.info(
                            f"Auto-cleared quarantine for {version_id}: verification passed"
                        )
                        # Recalculate latest_version_id so the package page shows data
                        from app.packages.version_queries import recalculate_latest_version_id
                        await recalculate_latest_version_id(session, pv.package_id)

                        # Load package + publisher eagerly in one query. P1-L5:
                        # avoid relying on sync lazy-loading inside async code,
                        # which raised `MissingGreenlet` under Meilisearch-sync
                        # refreshes that fired after the event-loop boundary.
                        from sqlalchemy.orm import selectinload
                        pkg_result2 = await session.execute(
                            select(Package)
                            .options(selectinload(Package.publisher))
                            .where(Package.id == pv.package_id)
                        )
                        pkg2 = pkg_result2.scalar_one_or_none()
                        if pkg2:
                            # P1-V3: packages_cleared_count counts MANUAL
                            # admin clears only. Auto-clearing on a passing
                            # verification run must NOT inflate the publisher
                            # reputation metric — that would let a publisher
                            # farm the counter by publishing then re-verifying
                            # trivial packages. The counter is incremented in
                            # the admin manual-clear endpoint instead.

                            # Sync to Meilisearch now that version is public
                            from app.packages.service import build_meili_document
                            from app.shared.meili import sync_package_to_meilisearch
                            await sync_package_to_meilisearch(build_meili_document(pkg2, pv, pv.manifest_raw or {}))
                    else:
                        logger.info(
                            f"Quarantine NOT auto-cleared for {version_id}: "
                            f"reason '{reason}' is not verification-related"
                        )

                await session.commit()

                # P1-V2: persist an admin audit log entry on admin-triggered
                # reverifies so the `admin_audit_logs` table has both the
                # trigger AND the outcome side-by-side. The trigger-side row
                # is written by the admin endpoint before it fires off the
                # background task; this completes the pair.
                if triggered_by == "admin_reverify" and admin_user_id is not None:
                    try:
                        from app.admin.models import AdminAuditLog
                        session.add(AdminAuditLog(
                            admin_user_id=admin_user_id,
                            action="reverify_version_completed",
                            target_type="package_version",
                            target_id=str(version_id),
                            metadata_={
                                "outcome": final_status,
                                "score": score_result.score,
                                "tier": pv.verification_tier,
                                "install_status": install_status,
                                "import_status": import_status,
                                "smoke_status": step_results["smoke_status"],
                                "tests_status": step_results["tests_status"],
                                "duration_ms": duration_ms,
                            },
                        ))
                        await session.commit()
                    except Exception:
                        logger.exception("Failed to write admin_reverify audit log")

                # Log verification_passed for candidate tracking (if applicable)
                if final_status == "passed":
                    try:
                        await _log_candidate_verification(pv.package_id)
                    except Exception:
                        logger.exception("Failed to log candidate verification event")

                logger.info(
                    f"Verification for {version_id}: {final_status} "
                    f"(install={install_status}, import={import_status}, "
                    f"smoke={step_results['smoke_status']}, tests={step_results['tests_status']}) "
                    f"in {duration_ms}ms"
                )

                # Send email on failure — different emails depending on context
                if final_status == "failed":
                    try:
                        pkg_result = await session.execute(
                            select(Package).where(Package.id == pv.package_id)
                        )
                        pkg = pkg_result.scalar_one_or_none()
                        if pkg:
                            from app.shared.email import (
                                get_publisher_email,
                                send_auto_quarantine_email,
                                send_verification_failed_email,
                            )
                            pub_email = await get_publisher_email(pkg.publisher_id)
                            if pub_email:
                                if triggered_by == "publish" and pv.quarantine_status == "quarantined":
                                    # Actual quarantine happened — send quarantine email
                                    await send_auto_quarantine_email(
                                        pub_email, pkg.slug, pv.version_number, 0
                                    )
                                else:
                                    # Re-verify or non-quarantine failure — send informative email
                                    await send_verification_failed_email(
                                        pub_email, pkg.slug, pv.version_number,
                                        step_results.get("error_summary") or "Unknown error",
                                    )
                    except Exception:
                        logger.exception("Failed to send verification failure email")

        except asyncio.TimeoutError:
            logger.error(f"Verification timed out for {version_id} ({settings.VERIFICATION_TIMEOUT}s)")
            try:
                async with async_session_factory() as session:
                    from app.packages.models import PackageVersion
                    from app.verification.models import VerificationResult

                    pv = (await session.execute(
                        select(PackageVersion).where(PackageVersion.id == version_id)
                    )).scalar_one_or_none()

                    # Find the latest running result for this version
                    vr = (await session.execute(
                        select(VerificationResult)
                        .where(VerificationResult.package_version_id == version_id)
                        .order_by(VerificationResult.created_at.desc())
                        .limit(1)
                    )).scalar_one_or_none()

                    if vr and vr.status == "running":
                        vr.status = "error"
                        vr.error_summary = f"Verification timed out after {settings.VERIFICATION_TIMEOUT}s"
                        vr.completed_at = datetime.now(timezone.utc)
                    if pv:
                        pv.verification_status = "error"
                    await session.commit()
            except Exception:
                logger.exception("Failed to update verification status after timeout")

        except Exception:
            logger.exception(f"Verification pipeline failed for version {version_id}")
            try:
                async with async_session_factory() as session:
                    from app.packages.models import PackageVersion
                    from app.verification.models import VerificationResult

                    pv = (await session.execute(
                        select(PackageVersion).where(PackageVersion.id == version_id)
                    )).scalar_one_or_none()

                    vr = (await session.execute(
                        select(VerificationResult)
                        .where(VerificationResult.package_version_id == version_id)
                        .order_by(VerificationResult.created_at.desc())
                        .limit(1)
                    )).scalar_one_or_none()

                    if vr and vr.status == "running":
                        vr.status = "error"
                        vr.error_summary = "Internal verification error"
                        vr.completed_at = datetime.now(timezone.utc)
                    if pv:
                        pv.verification_status = "error"
                    await session.commit()
            except Exception:
                logger.exception("Failed to update verification status after error")
