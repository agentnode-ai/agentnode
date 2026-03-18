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
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select

from app.config import settings
from app.database import async_session_factory

logger = logging.getLogger(__name__)

RUNNER_VERSION = "1.0.0"

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
    from app.verification.sandbox import VerificationSandbox
    from app.verification.steps import step_import, step_install, step_smoke, step_tests

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
        "error_summary": None,
        "warnings_count": 0,
        "warnings_summary": None,
    }

    try:
        # Extract artifact
        if not sandbox.extract_artifact(artifact_bytes):
            result["install_status"] = "error"
            result["install_log"] = "Failed to extract artifact"
            result["error_summary"] = "Artifact extraction failed"
            return result

        # Step 1: Install
        ok, log = step_install(sandbox)
        result["install_status"] = "passed" if ok else "failed"
        result["install_log"] = log
        if not ok:
            result["error_summary"] = "Package installation failed"
            return result

        # Step 2: Import
        ok, log = step_import(sandbox, tools)
        result["import_status"] = "passed" if ok else "failed"
        result["import_log"] = log
        if not ok:
            result["error_summary"] = "Tool entrypoint import verification failed"
            return result

        # Step 3: Smoke test (returns status string, not bool)
        smoke_status, log = step_smoke(sandbox, tools)
        result["smoke_status"] = smoke_status
        result["smoke_log"] = log

        # Step 4: Tests
        if sandbox.has_tests():
            ok, log = step_tests(sandbox)
            result["tests_status"] = "passed" if ok else "failed"
            result["tests_log"] = log
        else:
            result["tests_status"] = "not_present"
            result["tests_log"] = "No test directory found"

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


async def run_verification(version_id: UUID, triggered_by: str = "publish") -> None:
    """Run verification pipeline on a published package version.

    Called as a background task after publish, parallel to security scan.
    Uses a semaphore to limit concurrent verifications on single-VPS.
    Creates a new VerificationResult row each run (append-only history).

    Args:
        version_id: The package version to verify.
        triggered_by: "publish", "admin_reverify", or "runner_upgrade".
    """
    if not settings.VERIFICATION_ENABLED:
        return

    async with _get_semaphore():
        try:
            async with async_session_factory() as session:
                from app.packages.models import Capability, Package, PackageVersion
                from app.verification.models import VerificationResult

                # Load package version
                pv_result = await session.execute(
                    select(PackageVersion).where(PackageVersion.id == version_id)
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

                # Always create a NEW result row (append-only history)
                vr = VerificationResult(package_version_id=version_id)
                vr.status = "running"
                vr.started_at = datetime.now(timezone.utc)
                vr.runner_version = RUNNER_VERSION
                vr.python_version = sys.version
                vr.runner_platform = platform.platform()
                vr.triggered_by = triggered_by
                session.add(vr)

                pv.verification_status = "running"
                await session.commit()

                # Download artifact
                from app.shared.storage import download_artifact
                artifact_bytes = download_artifact(pv.artifact_object_key)

                # Gather tool entrypoints from capabilities
                caps_result = await session.execute(
                    select(Capability).where(
                        Capability.package_version_id == version_id,
                        Capability.capability_type == "tool",
                    )
                )
                capabilities = caps_result.scalars().all()
                tools = [
                    {
                        "name": cap.name,
                        "entrypoint": cap.entrypoint,
                        "input_schema": cap.input_schema,
                    }
                    for cap in capabilities
                    if cap.entrypoint
                ]

                # Run verification in thread pool (subprocess.run blocks)
                start_time = time.monotonic()
                loop = asyncio.get_event_loop()
                step_results = await asyncio.wait_for(
                    loop.run_in_executor(
                        None, _run_verification_sync, artifact_bytes, tools
                    ),
                    timeout=settings.VERIFICATION_TIMEOUT,
                )
                duration_ms = int((time.monotonic() - start_time) * 1000)

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
                vr.error_summary = step_results["error_summary"]
                vr.warnings_count = step_results["warnings_count"]
                vr.warnings_summary = step_results["warnings_summary"]

                await session.flush()

                # Update denormalized fields on PackageVersion
                pv.verification_status = final_status
                pv.latest_verification_result_id = vr.id
                pv.verification_run_count = (pv.verification_run_count or 0) + 1
                pv.last_verified_at = datetime.now(timezone.utc)

                # Auto-quarantine on install/import failure
                if final_status == "failed" and pv.quarantine_status == "none":
                    pv.quarantine_status = "quarantined"
                    pv.quarantined_at = datetime.now(timezone.utc)
                    pv.quarantine_reason = (
                        f"Auto-quarantined: verification failed ({step_results['error_summary']})"
                    )
                    logger.warning(
                        f"Auto-quarantined {version_id}: {step_results['error_summary']}"
                    )

                await session.commit()
                logger.info(
                    f"Verification for {version_id}: {final_status} "
                    f"(install={install_status}, import={import_status}, "
                    f"smoke={step_results['smoke_status']}, tests={step_results['tests_status']}) "
                    f"in {duration_ms}ms"
                )

                # Send email on failure
                if final_status == "failed":
                    try:
                        pkg_result = await session.execute(
                            select(Package).where(Package.id == pv.package_id)
                        )
                        pkg = pkg_result.scalar_one_or_none()
                        if pkg:
                            from app.shared.email import get_publisher_email, send_auto_quarantine_email
                            pub_email = await get_publisher_email(pkg.publisher_id)
                            if pub_email:
                                await send_auto_quarantine_email(
                                    pub_email, pkg.slug, pv.version_number, 0
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
