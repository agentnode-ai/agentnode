"""Security scanner — runs async after publish.
Spec section 10.

MVP: best-effort heuristic, not a security guarantee."""

from __future__ import annotations

import logging
import re
from uuid import UUID

from sqlalchemy import select

from app.database import async_session_factory

logger = logging.getLogger(__name__)

SECRET_PATTERNS = [
    (r'(?i)(api[_-]?key|secret|token|password)\s*[=:]\s*["\'][^"\']{8,}', "secret_detected"),
    (r'(?i)sk-[a-zA-Z0-9]{20,}', "secret_detected"),
    (r'(?i)ghp_[a-zA-Z0-9]{36}', "secret_detected"),
    (r'(?i)AKIA[0-9A-Z]{16}', "secret_detected"),
    (r'(?i)-----BEGIN (RSA |EC )?PRIVATE KEY-----', "secret_detected"),
]

DANGEROUS_PATTERNS = [
    (r'subprocess\.(run|Popen|call)\(', "undeclared_code_execution"),
    (r'os\.system\(', "undeclared_code_execution"),
    (r'exec\(', "undeclared_code_execution"),
    (r'eval\(', "undeclared_code_execution"),
    (r'__import__\(', "undeclared_code_execution"),
    (r'shutil\.rmtree\(', "undeclared_code_execution"),
]

NETWORK_PATTERNS = [
    (r'requests\.(get|post|put|delete)\(', "undeclared_network_access"),
    (r'httpx\.', "undeclared_network_access"),
    (r'urllib\.request', "undeclared_network_access"),
    (r'socket\.', "undeclared_network_access"),
]


async def run_security_scan(version_id: UUID) -> None:
    """Run security scan on a published package version.
    Called as a background task after publish."""
    try:
        async with async_session_factory() as session:
            from app.packages.models import PackageVersion, Permission, SecurityFinding

            result = await session.execute(
                select(PackageVersion)
                .where(PackageVersion.id == version_id)
            )
            pv = result.scalar_one_or_none()
            if not pv or not pv.manifest_raw:
                return

            # Get permissions for context
            perm_result = await session.execute(
                select(Permission)
                .where(Permission.package_version_id == version_id)
            )
            perm = perm_result.scalar_one_or_none()

            # Scan the manifest raw content for patterns
            manifest_str = str(pv.manifest_raw)
            findings = []

            # Check secrets
            for pattern, finding_type in SECRET_PATTERNS:
                if re.search(pattern, manifest_str):
                    findings.append(("high", finding_type, f"Pattern match: {pattern[:40]}"))

            # Check dangerous patterns (only flag if permissions say "none")
            if perm and perm.code_execution_level == "none":
                for pattern, finding_type in DANGEROUS_PATTERNS:
                    if re.search(pattern, manifest_str):
                        findings.append(("medium", finding_type, "Code execution pattern found but permissions declare none"))

            # Check network patterns
            if perm and perm.network_level == "none":
                for pattern, finding_type in NETWORK_PATTERNS:
                    if re.search(pattern, manifest_str):
                        findings.append(("medium", finding_type, "Network access pattern found but permissions declare none"))

            # Save findings
            for severity, finding_type, description in findings:
                session.add(SecurityFinding(
                    package_version_id=version_id,
                    severity=severity,
                    finding_type=finding_type,
                    description=description,
                    scanner="agentnode-static-v1",
                ))

            await session.commit()
            logger.info(f"Security scan complete for version {version_id}: {len(findings)} findings")

    except Exception:
        logger.exception(f"Security scan failed for version {version_id}")
