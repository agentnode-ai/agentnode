"""security_scanner_agent — AgentNode agent v2

Security Scanner Agent: Run comprehensive security scan: SAST, dependency vulnerabilities, secret detection.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _call(ctx, slug, tool_name=None, **kw):
    """Call a tool via AgentContext. Returns (success: bool, data: dict)."""
    r = ctx.run_tool(slug, tool_name, **kw)
    if r.success:
        return True, (r.result if isinstance(r.result, dict) else {"output": r.result})
    return False, {"error": r.error or "unknown"}


def run(context: Any, **kwargs: Any) -> dict:
    """Agent entrypoint — AgentContext contract v1.

    Args:
        context: AgentContext with goal, run_tool(), next_iteration().
        **kwargs: Additional parameters from the caller.

    Returns:
        Structured result dict.
    """
    code = kwargs.get("code", "") or context.goal

    # Step 1: Static analysis (lint)
    context.next_iteration()
    ok, lint = _call(context, "code-linter-pack", "code_analysis",
                     code=code, language="python")
    lint_findings = lint if ok else {}

    # Step 2: Security-specific audit (bandit)
    context.next_iteration()
    ok, security = _call(context, "security-audit-pack", "code_analysis",
                         code=code, severity="LOW")
    security_findings = security if ok else {}

    # Step 3: Secret scanning
    context.next_iteration()
    ok, secrets = _call(context, "secret-scanner-pack", "code_analysis", code=code)
    secrets_findings = secrets if ok else {}

    # Step 4: Search for known issues
    context.next_iteration()
    # Extract imports to check for vulnerable packages
    import_lines = [l.strip() for l in code.split("\n")
                    if l.strip().startswith("import ") or l.strip().startswith("from ")]
    vuln_info = []
    if import_lines:
        pkgs = " ".join(import_lines[:5])
        ok, search = _call(context, "web-search-pack", "search_web",
                           query=f"python security vulnerability {pkgs[:100]}",
                           max_results=3)
        if ok:
            vuln_info = search.get("results", [])

    # Severity breakdown
    total_issues = 0
    for findings in [lint_findings, security_findings, secrets_findings]:
        if isinstance(findings, dict):
            issues = findings.get("issues", findings.get("findings", []))
            if isinstance(issues, list):
                total_issues += len(issues)

    return {"scan_results": {"lint": lint_findings, "security": security_findings,
                             "secrets": secrets_findings},
            "known_vulnerabilities": [{"title": v.get("title", ""), "url": v.get("url", "")}
                                      for v in vuln_info],
            "total_issues": total_issues,
            "done": True}
