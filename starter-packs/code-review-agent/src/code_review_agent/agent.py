"""code_review_agent — AgentNode agent v2

Code Review Agent: Perform comprehensive code review: lint, security audit, and refactoring suggestions.
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
    code = kwargs.get("code", "") or context.goal

    # Step 1: Lint the code
    context.next_iteration()
    ok, lint = _call(context, "code-linter-pack", "code_analysis",
                     code=code, language="python")
    lint_result = lint if ok else {"error": "Linting failed"}

    # Step 2: Security audit
    context.next_iteration()
    ok, security = _call(context, "security-audit-pack", "code_analysis",
                         code=code, severity="LOW")
    security_result = security if ok else {"error": "Security audit failed"}

    # Step 3: Refactoring analysis
    context.next_iteration()
    ok, refactor = _call(context, "code-refactor-pack", "code_analysis",
                         code=code, operation="analyze")
    refactor_result = refactor if ok else {"error": "Refactoring analysis failed"}

    # Step 4: Scan for secrets
    context.next_iteration()
    ok, secrets = _call(context, "secret-scanner-pack", "code_analysis", code=code)
    secrets_result = secrets if ok else {}

    # Compile review
    review_sections = []
    if lint_result and "error" not in lint_result:
        review_sections.append(f"## Linting\n{lint_result}")
    if security_result and "error" not in security_result:
        review_sections.append(f"## Security\n{security_result}")
    if refactor_result and "error" not in refactor_result:
        review_sections.append(f"## Refactoring\n{refactor_result}")
    if secrets_result:
        review_sections.append(f"## Secrets Scan\n{secrets_result}")

    return {"review": "\n\n".join(review_sections),
            "lint": lint_result, "security": security_result,
            "refactoring": refactor_result, "secrets": secrets_result,
            "done": True}
