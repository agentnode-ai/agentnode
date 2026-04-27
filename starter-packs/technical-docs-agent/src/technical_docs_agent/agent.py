"""technical_docs_agent — AgentNode agent v2

Technical Documentation Agent: Generate API documentation and developer guides from source code.
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

    # Step 1: Analyze code structure
    context.next_iteration()
    ok, analysis = _call(context, "code-refactor-pack", None,
                         code=code, operation="analyze")
    code_structure = analysis if ok else {}

    # Step 2: Generate test stubs to understand function signatures
    context.next_iteration()
    ok, tests = _call(context, "test-generator-pack", None,
                      code=code, framework="pytest")
    test_stubs = tests if ok else {}

    # Step 3: Lint code to find documentation gaps
    context.next_iteration()
    ok, lint = _call(context, "code-linter-pack", None,
                     code=code, language="python")
    lint_issues = lint if ok else {}

    # Assemble documentation
    doc = "# API Documentation\n\n"

    if code_structure:
        doc += "## Code Structure\n\n"
        for key, val in code_structure.items():
            if key != "error":
                doc += f"- **{key}**: {val}\n"
        doc += "\n"

    if test_stubs:
        doc += "## Function Signatures\n\n"
        test_code = test_stubs.get("tests", test_stubs.get("output", ""))
        if isinstance(test_code, str):
            doc += f"```python\n{test_code[:2000]}\n```\n\n"

    if lint_issues:
        doc += "## Quality Notes\n\n"
        issues = lint_issues.get("issues", lint_issues.get("output", ""))
        if isinstance(issues, (list, str)):
            doc += f"{issues}\n"

    return {"documentation": doc, "code_structure": code_structure,
            "test_stubs": test_stubs, "done": True}
