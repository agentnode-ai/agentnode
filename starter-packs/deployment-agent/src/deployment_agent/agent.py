"""deployment_agent — AgentNode agent v2

Deployment Agent: Orchestrate deployments: verify code quality, run checks, and produce a deployment checklist.
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

    # Step 1: Lint code quality
    context.next_iteration()
    ok, lint = _call(context, "code-linter-pack", "code_analysis",
                     code=code, language="python")
    lint_ok = ok and not lint.get("issues", [])
    lint_result = lint if ok else {"error": "Lint failed"}

    # Step 2: Security audit
    context.next_iteration()
    ok, security = _call(context, "security-audit-pack", "code_analysis",
                         code=code, severity="MEDIUM")
    security_ok = ok and not security.get("issues", [])
    security_result = security if ok else {"error": "Security audit failed"}

    # Step 3: Secret scan
    context.next_iteration()
    ok, secrets = _call(context, "secret-scanner-pack", "code_analysis", code=code)
    secrets_ok = ok and not secrets.get("findings", secrets.get("secrets", []))
    secrets_result = secrets if ok else {}

    # Step 4: Generate test status
    context.next_iteration()
    ok, tests = _call(context, "test-generator-pack", "code_analysis",
                      code=code, framework="pytest")
    tests_result = tests if ok else {}

    # Build deployment checklist
    checks = [
        {"check": "Code linting", "passed": lint_ok},
        {"check": "Security audit", "passed": security_ok},
        {"check": "Secret scanning", "passed": secrets_ok},
        {"check": "Test generation", "passed": bool(tests_result.get("tests", tests_result.get("output", "")))},
    ]
    all_passed = all(c["passed"] for c in checks)

    return {"ready_to_deploy": all_passed,
            "checklist": checks,
            "lint": lint_result, "security": security_result,
            "secrets": secrets_result, "tests": tests_result,
            "done": True}
