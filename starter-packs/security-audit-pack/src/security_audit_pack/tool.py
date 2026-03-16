"""Run security audits on Python code using bandit."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path


def run(code: str, severity: str = "LOW") -> dict:
    """Run bandit security analysis on Python code.

    Args:
        code: Python source code to audit.
        severity: Minimum severity level to report - "LOW", "MEDIUM", or "HIGH".

    Returns:
        dict with keys: issues, total.
    """
    bandit_bin = shutil.which("bandit")
    if bandit_bin is None:
        return {
            "issues": [{"severity": "ERROR", "confidence": "HIGH", "description": "bandit is not installed or not on PATH.", "line": 0}],
            "total": 1,
        }

    # Validate severity
    severity = severity.upper()
    if severity not in ("LOW", "MEDIUM", "HIGH"):
        severity = "LOW"

    # Map severity to bandit's -l flag levels
    severity_flags = {
        "LOW": ["-l"],       # low and above
        "MEDIUM": ["-ll"],   # medium and above
        "HIGH": ["-lll"],    # high only
    }

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    )
    try:
        tmp.write(code)
        tmp.flush()
        tmp.close()

        cmd = [
            bandit_bin,
            "-f", "json",
            *severity_flags[severity],
            str(tmp.name),
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )

        issues = _parse_bandit_output(result.stdout)

        return {
            "issues": issues,
            "total": len(issues),
        }

    except subprocess.TimeoutExpired:
        return {
            "issues": [{"severity": "ERROR", "confidence": "HIGH", "description": "Bandit timed out after 60 seconds.", "line": 0}],
            "total": 1,
        }
    except Exception as exc:
        return {
            "issues": [{"severity": "ERROR", "confidence": "HIGH", "description": f"Unexpected error: {exc}", "line": 0}],
            "total": 1,
        }
    finally:
        Path(tmp.name).unlink(missing_ok=True)


def _parse_bandit_output(output: str) -> list[dict]:
    """Parse bandit's JSON output into a clean issues list."""
    if not output.strip():
        return []

    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return [{"severity": "ERROR", "confidence": "LOW", "description": "Failed to parse bandit output.", "line": 0}]

    issues: list[dict] = []

    for result in data.get("results", []):
        issues.append({
            "severity": result.get("issue_severity", "UNKNOWN"),
            "confidence": result.get("issue_confidence", "UNKNOWN"),
            "description": result.get("issue_text", ""),
            "line": result.get("line_number", 0),
            "test_id": result.get("test_id", ""),
            "test_name": result.get("test_name", ""),
        })

    return issues
