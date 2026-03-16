"""Security scanner — runs async after publish.
Spec section 10.

Combines heuristic regex patterns with optional bandit AST analysis
for more reliable detection of security issues.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select

from app.database import async_session_factory

logger = logging.getLogger(__name__)

SECRET_PATTERNS = [
    r'(?i)(api[_-]?key|secret|token|password)\s*[=:]\s*["\'][^"\']{8,}',
    r'(?i)sk-[a-zA-Z0-9]{20,}',           # OpenAI
    r'(?i)ghp_[a-zA-Z0-9]{36}',           # GitHub
    r'(?i)AKIA[0-9A-Z]{16}',              # AWS
    r'(?i)-----BEGIN (RSA |EC )?PRIVATE KEY-----',
]

DANGEROUS_PATTERNS = [
    r'subprocess\.(run|Popen|call)\(',
    r'os\.system\(', r'exec\(', r'eval\(',
    r'__import__\(', r'shutil\.rmtree\(',
]

NETWORK_PATTERNS = [
    r'requests\.(get|post|put|delete)\(',
    r'httpx\.', r'urllib\.request', r'socket\.',
]

# Bandit severity mapping
BANDIT_SEVERITY_MAP = {
    "HIGH": "high",
    "MEDIUM": "medium",
    "LOW": "low",
}


def _scan_content(content: str, file_path: str) -> list[dict]:
    """Scan a single file's content against all pattern groups."""
    findings: list[dict] = []
    for line_no, line in enumerate(content.splitlines(), start=1):
        for pattern in SECRET_PATTERNS:
            if re.search(pattern, line):
                findings.append({
                    "severity": "high",
                    "finding_type": "secret_detected",
                    "description": f"Potential secret in {file_path}:{line_no}",
                    "category": "secret",
                })
        for pattern in DANGEROUS_PATTERNS:
            if re.search(pattern, line):
                findings.append({
                    "severity": "medium",
                    "finding_type": "undeclared_code_execution",
                    "description": f"Code execution pattern in {file_path}:{line_no}",
                    "category": "dangerous",
                })
        for pattern in NETWORK_PATTERNS:
            if re.search(pattern, line):
                findings.append({
                    "severity": "medium",
                    "finding_type": "undeclared_network_access",
                    "description": f"Network access pattern in {file_path}:{line_no}",
                    "category": "network",
                })
    return findings


def _run_bandit(scan_dir: str) -> list[dict]:
    """Run bandit static analysis if available. Returns findings."""
    findings: list[dict] = []
    try:
        result = subprocess.run(
            ["bandit", "-r", scan_dir, "-f", "json", "-ll", "--quiet"],
            capture_output=True, text=True, timeout=60,
        )
        if result.stdout:
            bandit_output = json.loads(result.stdout)
            for issue in bandit_output.get("results", []):
                severity = BANDIT_SEVERITY_MAP.get(issue.get("issue_severity", ""), "medium")
                findings.append({
                    "severity": severity,
                    "finding_type": f"bandit_{issue.get('test_id', 'unknown')}",
                    "description": (
                        f"[{issue.get('test_id')}] {issue.get('issue_text', 'Security issue')} "
                        f"in {issue.get('filename', '?')}:{issue.get('line_number', '?')}"
                    ),
                    "category": "bandit",
                })
    except FileNotFoundError:
        logger.debug("bandit not installed, skipping AST analysis")
    except subprocess.TimeoutExpired:
        logger.warning("bandit scan timed out after 60s")
    except Exception as e:
        logger.warning(f"bandit scan error: {e}")
    return findings


def _extract_and_scan(artifact_bytes: bytes) -> list[dict]:
    """Extract a tar.gz artifact and scan all .py files."""
    all_findings: list[dict] = []
    tmp_dir = tempfile.mkdtemp(prefix="agentnode_scan_")
    try:
        with tarfile.open(fileobj=io.BytesIO(artifact_bytes), mode="r:gz") as tar:
            for member in tar.getmembers():
                if os.path.normpath(member.name).startswith("..") or os.path.isabs(member.name):
                    continue
            tar.extractall(tmp_dir, filter="data")

        # Heuristic regex scan
        for root, _dirs, files in os.walk(tmp_dir):
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                full_path = os.path.join(root, fname)
                rel_path = os.path.relpath(full_path, tmp_dir)
                try:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                        all_findings.extend(_scan_content(f.read(), rel_path))
                except Exception:
                    logger.warning(f"Could not read file for scanning: {rel_path}")

        # AST-based scan with bandit
        bandit_findings = _run_bandit(tmp_dir)
        all_findings.extend(bandit_findings)

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return all_findings


async def run_security_scan(version_id: UUID) -> None:
    """Run security scan on a published package version.
    Called as a background task after publish."""
    try:
        async with async_session_factory() as session:
            from app.packages.models import PackageVersion, Permission, SecurityFinding

            result = await session.execute(
                select(PackageVersion).where(PackageVersion.id == version_id)
            )
            pv = result.scalar_one_or_none()
            if not pv:
                return

            perm_result = await session.execute(
                select(Permission).where(Permission.package_version_id == version_id)
            )
            perm = perm_result.scalar_one_or_none()

            raw_findings: list[dict] = []

            # Scan artifact .py files
            if pv.artifact_object_key:
                try:
                    from app.shared.storage import download_artifact
                    raw_findings.extend(_extract_and_scan(download_artifact(pv.artifact_object_key)))
                except Exception:
                    logger.exception(f"Failed to download/scan artifact for {version_id}")

            # Also scan manifest as fallback
            if pv.manifest_raw:
                raw_findings.extend(_scan_content(str(pv.manifest_raw), "<manifest>"))

            # Filter based on declared permissions
            filtered: list[dict] = []
            for finding in raw_findings:
                cat = finding.pop("category")
                if cat == "secret":
                    filtered.append(finding)
                elif cat == "dangerous" and (not perm or perm.code_execution_level == "none"):
                    filtered.append(finding)
                elif cat == "network" and (not perm or perm.network_level == "none"):
                    filtered.append(finding)

            has_high = False
            for f in filtered:
                if f["severity"] == "high":
                    has_high = True
                session.add(SecurityFinding(
                    package_version_id=version_id,
                    severity=f["severity"],
                    finding_type=f["finding_type"],
                    description=f["description"],
                    scanner="agentnode-static-v1",
                ))

            # Auto-quarantine on high-severity findings
            if has_high and pv.quarantine_status == "none":
                pv.quarantine_status = "quarantined"
                pv.quarantined_at = datetime.now(timezone.utc)
                pv.quarantine_reason = f"Auto-quarantined: {sum(1 for f in filtered if f['severity'] == 'high')} high-severity finding(s)"

            await session.commit()
            logger.info(f"Security scan for {version_id}: {len(filtered)} finding(s)")

    except Exception:
        logger.exception(f"Security scan failed for version {version_id}")
