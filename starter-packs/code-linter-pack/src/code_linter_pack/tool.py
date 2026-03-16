"""Lint Python code using ruff and optionally auto-fix issues."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path


def run(code: str, language: str = "python", fix: bool = False) -> dict:
    """Lint Python code with ruff.

    Args:
        code: The Python source code to lint.
        language: Programming language (only "python" supported).
        fix: If True, apply ruff's auto-fixes and return the fixed code.

    Returns:
        dict with keys: issues, issue_count, fixed_code.
    """
    if language != "python":
        return {
            "issues": [],
            "issue_count": 0,
            "fixed_code": None,
        }

    ruff_bin = shutil.which("ruff")
    if ruff_bin is None:
        return {
            "issues": [{"line": 0, "code": "E000", "message": "ruff is not installed or not on PATH."}],
            "issue_count": 1,
            "fixed_code": None,
        }

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    )
    try:
        tmp.write(code)
        tmp.flush()
        tmp.close()
        tmp_path = Path(tmp.name)

        # Run ruff check with JSON output
        issues = _check(ruff_bin, tmp_path)

        fixed_code = None
        if fix:
            fixed_code = _fix(ruff_bin, tmp_path, code)

        return {
            "issues": issues,
            "issue_count": len(issues),
            "fixed_code": fixed_code,
        }

    finally:
        Path(tmp.name).unlink(missing_ok=True)


def _check(ruff_bin: str, file_path: Path) -> list[dict]:
    """Run ruff check and parse output into a list of issues."""
    result = subprocess.run(
        [ruff_bin, "check", "--output-format", "json", "--no-fix", str(file_path)],
        capture_output=True,
        text=True,
    )

    issues: list[dict] = []
    output = result.stdout.strip()
    if not output:
        return issues

    try:
        raw_issues = json.loads(output)
    except json.JSONDecodeError:
        # Fallback: parse line-based output
        return _parse_text_output(result.stdout)

    for item in raw_issues:
        issues.append({
            "line": item.get("location", {}).get("row", 0),
            "code": item.get("code", ""),
            "message": item.get("message", ""),
        })

    return issues


def _fix(ruff_bin: str, file_path: Path, original_code: str) -> str:
    """Run ruff fix on a copy of the file and return fixed code."""
    fix_tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    )
    try:
        fix_tmp.write(original_code)
        fix_tmp.flush()
        fix_tmp.close()
        fix_path = Path(fix_tmp.name)

        subprocess.run(
            [ruff_bin, "check", "--fix", str(fix_path)],
            capture_output=True,
            text=True,
        )

        return fix_path.read_text(encoding="utf-8")
    finally:
        Path(fix_tmp.name).unlink(missing_ok=True)


def _parse_text_output(text: str) -> list[dict]:
    """Fallback parser for non-JSON ruff output."""
    issues: list[dict] = []
    for line in text.strip().splitlines():
        # Format: path:line:col: CODE message
        parts = line.split(":", 3)
        if len(parts) >= 4:
            try:
                line_no = int(parts[1])
            except ValueError:
                line_no = 0
            remainder = parts[3].strip()
            code = remainder.split(" ", 1)[0] if " " in remainder else remainder
            message = remainder.split(" ", 1)[1] if " " in remainder else ""
            issues.append({"line": line_no, "code": code, "message": message})
    return issues
