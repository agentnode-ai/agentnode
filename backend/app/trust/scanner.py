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

ENV_ACCESS_PATTERNS = [
    r'os\.environ',              # os.environ["KEY"] or os.environ.get()
    r'os\.getenv\(',             # os.getenv("KEY")
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
        for pattern in ENV_ACCESS_PATTERNS:
            if re.search(pattern, line):
                findings.append({
                    "severity": "high",
                    "finding_type": "env_harvesting",
                    "description": f"Environment variable access in {file_path}:{line_no} — potential data exfiltration risk",
                    "category": "env_access",
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
            safe_members = [
                m for m in tar.getmembers()
                if not os.path.normpath(m.name).startswith("..") and not os.path.isabs(m.name)
            ]
            tar.extractall(tmp_dir, members=safe_members, filter="data")

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
    Called as a background task after publish.

    Scan layers:
      1. Heuristic regex patterns (secrets, dangerous calls, network access)
      2. Bandit AST analysis (if installed)
      3. AI semantic analysis (for unverified/verified publishers, if API key set)
    """
    try:
        async with async_session_factory() as session:
            from app.packages.models import Package, PackageVersion, Permission, SecurityFinding
            from app.publishers.models import Publisher

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
            artifact_code_files: dict[str, str] = {}  # for AI scan

            # --- Layer 1+2: Regex + Bandit scan ---
            if pv.artifact_object_key:
                try:
                    from app.shared.storage import download_artifact
                    artifact_bytes = download_artifact(pv.artifact_object_key)
                    raw_findings.extend(_extract_and_scan(artifact_bytes))
                    # Also extract code files for AI scan
                    artifact_code_files = _extract_code_files(artifact_bytes)
                except Exception:
                    logger.exception(f"Failed to download/scan artifact for {version_id}")

            # Also scan manifest as fallback
            if pv.manifest_raw:
                raw_findings.extend(_scan_content(str(pv.manifest_raw), "<manifest>"))

            # --- Layer 3: AI semantic scan ---
            # Only for unverified/verified publishers (cost control)
            ai_findings: list[dict] = []
            if artifact_code_files and pv.manifest_raw:
                try:
                    pkg_result = await session.execute(
                        select(Package).where(Package.id == pv.package_id)
                    )
                    pkg = pkg_result.scalar_one_or_none()
                    if pkg:
                        pub_result = await session.execute(
                            select(Publisher).where(Publisher.id == pkg.publisher_id)
                        )
                        publisher = pub_result.scalar_one_or_none()
                        # Run AI scan for unverified and verified publishers
                        if publisher and publisher.trust_level in ("unverified", "verified"):
                            from app.trust.ai_scanner import ai_security_scan
                            manifest_dict = pv.manifest_raw if isinstance(pv.manifest_raw, dict) else {}
                            ai_findings = await ai_security_scan(manifest_dict, artifact_code_files)
                            logger.info(
                                "AI scan for %s: %d finding(s) (publisher: %s, trust: %s)",
                                version_id, len(ai_findings),
                                publisher.slug, publisher.trust_level,
                            )
                except Exception:
                    logger.exception(f"AI security scan failed for {version_id}")

            # Filter heuristic findings based on declared permissions
            filtered: list[dict] = []
            for finding in raw_findings:
                cat = finding.pop("category")
                if cat == "secret":
                    filtered.append(finding)
                elif cat == "env_access":
                    filtered.append(finding)
                elif cat == "dangerous" and (not perm or perm.code_execution_level == "none"):
                    filtered.append(finding)
                elif cat == "network" and (not perm or perm.network_level == "none"):
                    filtered.append(finding)

            # Add AI findings (already filtered by the AI scanner)
            for ai_f in ai_findings:
                ai_f.pop("category", None)
                filtered.append(ai_f)

            has_high = False
            has_critical = False
            for f in filtered:
                if f["severity"] == "high":
                    has_high = True
                if f["severity"] == "critical":
                    has_critical = True
                scanner_name = "agentnode-ai-v1" if f.get("finding_type", "").startswith("ai_") else "agentnode-static-v1"
                session.add(SecurityFinding(
                    package_version_id=version_id,
                    severity=f["severity"],
                    finding_type=f["finding_type"],
                    description=f["description"],
                    scanner=scanner_name,
                ))

            # Auto-quarantine on high/critical findings
            high_count = sum(1 for f in filtered if f["severity"] in ("high", "critical"))
            if (has_high or has_critical) and pv.quarantine_status == "none":
                pv.quarantine_status = "quarantined"
                pv.quarantined_at = datetime.now(timezone.utc)
                pv.quarantine_reason = f"Auto-quarantined: {high_count} high-severity finding(s)"

            await session.commit()
            logger.info(f"Security scan for {version_id}: {len(filtered)} finding(s) (static: {len(filtered) - len(ai_findings)}, ai: {len(ai_findings)})")

            # Send scan/quarantine emails
            if filtered:
                if not pkg:
                    pkg_result = await session.execute(
                        select(Package).where(Package.id == pv.package_id)
                    )
                    pkg = pkg_result.scalar_one_or_none()
                if pkg:
                    from app.shared.email import get_publisher_email
                    pub_email = await get_publisher_email(pkg.publisher_id)
                    if pub_email:
                        if has_high or has_critical:
                            from app.shared.email import send_auto_quarantine_email
                            await send_auto_quarantine_email(pub_email, pkg.slug, pv.version_number, high_count)
                        else:
                            from app.shared.email import send_security_scan_report_email
                            await send_security_scan_report_email(pub_email, pkg.slug, pv.version_number, len(filtered), high_count)

    except Exception:
        logger.exception(f"Security scan failed for version {version_id}")


def _extract_code_files(artifact_bytes: bytes) -> dict[str, str]:
    """Extract all .py files from a tar.gz artifact as {path: content} dict."""
    code_files: dict[str, str] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(artifact_bytes), mode="r:gz") as tar:
            for member in tar.getmembers():
                if not member.name.endswith(".py") or not member.isfile():
                    continue
                if os.path.normpath(member.name).startswith("..") or os.path.isabs(member.name):
                    continue
                f = tar.extractfile(member)
                if f:
                    try:
                        code_files[member.name] = f.read().decode("utf-8", errors="replace")
                    except Exception:
                        pass
    except Exception:
        logger.warning("Failed to extract code files from artifact")
    return code_files
