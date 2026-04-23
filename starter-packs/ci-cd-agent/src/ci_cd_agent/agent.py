"""ci_cd_agent — AgentNode agent v2

CI/CD Agent: Analyze project structure and generate CI/CD pipeline configuration.
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

    # Step 1: Analyze project structure
    context.next_iteration()
    ok, analysis = _call(context, "code-refactor-pack", "code_analysis",
                         code=code, operation="analyze")
    project_info = analysis if ok else {}

    # Step 2: Lint the code
    context.next_iteration()
    ok, lint = _call(context, "code-linter-pack", "code_analysis",
                     code=code, language="python", fix=False)
    lint_result = lint if ok else {}

    # Step 3: Generate test stubs
    context.next_iteration()
    ok, tests = _call(context, "test-generator-pack", "code_analysis",
                      code=code, framework="pytest")
    test_info = tests if ok else {}

    # Step 4: Generate pipeline recommendation
    context.next_iteration()
    pipeline_text = f"Project analysis: {project_info}\nLint status: {lint_result}"
    ok, summary = _call(context, "document-summarizer-pack", "document_summary",
                        text=pipeline_text, max_sentences=5)

    has_issues = bool(lint_result.get("issues", []))
    has_tests = bool(test_info.get("tests", test_info.get("output", "")))

    pipeline_steps = ["checkout", "install_dependencies"]
    if has_tests:
        pipeline_steps.append("run_tests")
    pipeline_steps.append("lint")
    if not has_issues:
        pipeline_steps.extend(["build", "deploy"])

    return {"pipeline_steps": pipeline_steps,
            "code_analysis": project_info,
            "lint_status": lint_result,
            "test_status": test_info,
            "recommendation": summary.get("summary", "") if ok else "",
            "ready_to_deploy": not has_issues,
            "done": True}
