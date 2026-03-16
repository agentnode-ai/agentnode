"""Scan code and directories for leaked secrets, API keys, and tokens."""

from __future__ import annotations

import math
import os
import re
from pathlib import Path


# ---------------------------------------------------------------------------
# Secret patterns
# ---------------------------------------------------------------------------

_PATTERNS: list[dict] = [
    {
        "type": "AWS Access Key",
        "regex": r"(?:AKIA)[A-Z0-9]{16}",
        "severity": "HIGH",
    },
    {
        "type": "AWS Secret Key",
        "regex": r"(?i)aws[_\-]?secret[_\-]?access[_\-]?key\s*[=:]\s*['\"]?([A-Za-z0-9/+=]{40})['\"]?",
        "severity": "CRITICAL",
    },
    {
        "type": "GitHub Token",
        "regex": r"gh[pousr]_[A-Za-z0-9_]{36,255}",
        "severity": "HIGH",
    },
    {
        "type": "GitHub Personal Access Token (classic)",
        "regex": r"ghp_[A-Za-z0-9]{36}",
        "severity": "HIGH",
    },
    {
        "type": "Generic API Key",
        "regex": r"(?i)(?:api[_\-]?key|apikey)\s*[=:]\s*['\"]?([A-Za-z0-9_\-]{20,64})['\"]?",
        "severity": "MEDIUM",
    },
    {
        "type": "Generic Secret",
        "regex": r"(?i)(?:secret|token|password|passwd|pwd)\s*[=:]\s*['\"]([^'\"]{8,})['\"]",
        "severity": "MEDIUM",
    },
    {
        "type": "Password in URL",
        "regex": r"[a-zA-Z]+://[^:\s]+:([^@\s]{8,})@",
        "severity": "HIGH",
    },
    {
        "type": "Private Key",
        "regex": r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----",
        "severity": "CRITICAL",
    },
    {
        "type": "JWT Token",
        "regex": r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_\-]{10,}",
        "severity": "HIGH",
    },
    {
        "type": "Slack Token",
        "regex": r"xox[bpsa]-[0-9]{10,}-[A-Za-z0-9\-]+",
        "severity": "HIGH",
    },
    {
        "type": "Google API Key",
        "regex": r"AIza[0-9A-Za-z\-_]{35}",
        "severity": "HIGH",
    },
    {
        "type": "Heroku API Key",
        "regex": r"(?i)heroku[_\-]?api[_\-]?key\s*[=:]\s*['\"]?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})['\"]?",
        "severity": "HIGH",
    },
    {
        "type": "Generic High-Entropy String",
        "regex": r"(?i)(?:key|secret|token|password|credential)\s*[=:]\s*['\"]([A-Za-z0-9+/=_\-]{32,})['\"]",
        "severity": "LOW",
    },
]

_COMPILED = [(p, re.compile(p["regex"])) for p in _PATTERNS]

# File extensions to scan
_SCAN_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".rb", ".go", ".java",
    ".yaml", ".yml", ".json", ".toml", ".ini", ".cfg", ".conf",
    ".env", ".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd",
    ".xml", ".properties", ".tf", ".hcl", ".dockerfile",
}

_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
}


def run(code: str | None = None, directory: str | None = None) -> dict:
    """Scan code or a directory for secret patterns.

    Args:
        code: Source code string to scan. Provide this OR directory.
        directory: Path to a directory to scan recursively.

    Returns:
        dict with keys: findings, total, severity.
    """
    if code is None and directory is None:
        return {"findings": [], "total": 0, "severity": "NONE"}

    findings: list[dict] = []

    if code is not None:
        findings.extend(_scan_text(code, source="<input>"))

    if directory is not None:
        dir_path = Path(directory)
        if dir_path.is_dir():
            findings.extend(_scan_directory(dir_path))
        else:
            return {
                "findings": [{"type": "error", "line": 0, "snippet": f"Directory not found: {directory}"}],
                "total": 1,
                "severity": "ERROR",
            }

    # Determine overall severity
    severity = "NONE"
    severity_order = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
    for f in findings:
        f_sev = f.get("severity", "LOW")
        if severity_order.get(f_sev, 0) > severity_order.get(severity, 0):
            severity = f_sev

    return {
        "findings": findings,
        "total": len(findings),
        "severity": severity,
    }


def _scan_text(text: str, source: str = "<input>") -> list[dict]:
    """Scan a text string for secrets."""
    findings: list[dict] = []
    lines = text.splitlines()

    for line_no, line in enumerate(lines, 1):
        for pattern_info, compiled in _COMPILED:
            for match in compiled.finditer(line):
                matched_str = match.group(0)

                # For generic patterns, verify entropy
                if pattern_info["type"].startswith("Generic") and pattern_info["severity"] == "LOW":
                    # Extract the captured group if present, else use full match
                    secret_part = match.group(1) if match.lastindex and match.lastindex >= 1 else matched_str
                    if _shannon_entropy(secret_part) < 3.5:
                        continue

                # Mask the secret in the snippet
                snippet = _mask_secret(line.strip(), matched_str)

                findings.append({
                    "type": pattern_info["type"],
                    "severity": pattern_info["severity"],
                    "line": line_no,
                    "file": source,
                    "snippet": snippet,
                })

    return findings


def _scan_directory(dir_path: Path) -> list[dict]:
    """Recursively scan a directory for secrets."""
    findings: list[dict] = []

    for root, dirs, files in os.walk(dir_path):
        # Prune directories
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]

        for filename in files:
            fpath = Path(root) / filename

            # Check extension or specific filenames
            if fpath.suffix.lower() not in _SCAN_EXTENSIONS and fpath.name not in (".env", "Dockerfile"):
                continue

            # Skip large files (> 1 MB)
            try:
                if fpath.stat().st_size > 1_048_576:
                    continue
            except OSError:
                continue

            try:
                text = fpath.read_text(encoding="utf-8", errors="ignore")
            except (OSError, UnicodeDecodeError):
                continue

            file_findings = _scan_text(text, source=str(fpath))
            findings.extend(file_findings)

    return findings


def _shannon_entropy(s: str) -> float:
    """Calculate Shannon entropy of a string."""
    if not s:
        return 0.0
    length = len(s)
    freq: dict[str, int] = {}
    for ch in s:
        freq[ch] = freq.get(ch, 0) + 1
    entropy = 0.0
    for count in freq.values():
        p = count / length
        if p > 0:
            entropy -= p * math.log2(p)
    return entropy


def _mask_secret(line: str, secret: str) -> str:
    """Mask the middle portion of a secret in its context."""
    if len(secret) <= 8:
        masked = secret[:2] + "***" + secret[-2:]
    else:
        masked = secret[:4] + "***" + secret[-4:]
    return line.replace(secret, masked, 1)
