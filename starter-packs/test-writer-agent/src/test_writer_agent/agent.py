"""test_writer_agent — AgentNode agent v2

Test Writer Agent: Analyze source code and generate comprehensive test suites with unit tests.
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
    framework = kwargs.get("framework", "pytest")

    # Step 1: Analyze code structure
    context.next_iteration()
    ok, analysis = _call(context, "code-refactor-pack", None,
                         code=code, operation="analyze")
    code_structure = analysis if ok else {}

    # Step 2: Generate tests
    context.next_iteration()
    ok, tests = _call(context, "test-generator-pack", None,
                      code=code, framework=framework)
    generated_tests = tests if ok else {"error": "Test generation failed"}

    # Step 3: Lint the generated tests
    test_code = ""
    if ok:
        test_code = tests.get("tests", tests.get("output", tests.get("code", "")))
        if isinstance(test_code, str) and test_code:
            context.next_iteration()
            ok, lint = _call(context, "code-linter-pack", None,
                             code=test_code, language="python")
            if ok and lint.get("issues"):
                generated_tests["lint_issues"] = lint["issues"]

    return {"tests": test_code, "code_structure": code_structure,
            "framework": framework, "generated": generated_tests,
            "done": True}
