"""dependency_audit_agent — AgentNode agent v2

Dependency Audit Agent: Scan project dependencies for vulnerabilities, outdated versions, and leaked secrets.
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

    Uses context.run_tool() for tool access.

    Args:
        context: AgentContext with goal and LLM/tool access.
        **kwargs: Additional parameters from the caller.

    Returns:
        Structured result dict.
    """
    code = kwargs.get("code", "") or context.goal  # requirements.txt or pyproject.toml content

    # Step 1: Analyze the dependency file
    context.next_iteration()
    ok, analysis = _call(context, "code-refactor-pack", "code_analysis",
                         code=code, operation="analyze")
    deps_info = analysis if ok else {}

    # Step 2: Search for known vulnerabilities
    context.next_iteration()
    # Extract package names from the code (rough heuristic)
    lines = code.strip().split("\n")
    packages = []
    for line in lines[:20]:
        line = line.strip()
        if line and not line.startswith("#") and not line.startswith("["):
            pkg = line.split(">=")[0].split("==")[0].split("<")[0].split(">")[0].strip()
            if pkg:
                packages.append(pkg)

    vulnerabilities = []
    for pkg in packages[:5]:
        context.next_iteration()
        ok, search = _call(context, "web-search-pack", "search_web",
                           query=f"{pkg} python CVE vulnerability 2025 2026",
                           max_results=3)
        if ok:
            for r in search.get("results", []):
                if any(kw in r.get("title", "").lower() for kw in ["cve", "vuln", "security"]):
                    vulnerabilities.append({
                        "package": pkg,
                        "title": r.get("title", ""),
                        "url": r.get("url", ""),
                    })

    # Step 3: Scan for leaked secrets
    context.next_iteration()
    ok, secrets = _call(context, "secret-scanner-pack", "code_analysis", code=code)
    secrets_found = secrets if ok else {}

    return {"packages_scanned": packages, "vulnerabilities": vulnerabilities,
            "secrets_scan": secrets_found, "dependency_info": deps_info,
            "done": True}
