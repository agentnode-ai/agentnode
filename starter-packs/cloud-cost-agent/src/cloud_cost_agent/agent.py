"""cloud_cost_agent — AgentNode agent v2

Cloud Cost Agent: Analyze cloud infrastructure costs, identify waste, and recommend optimizations.
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
    file_path = kwargs.get("file_path", "") or context.goal

    # Step 1: Describe the billing data
    context.next_iteration()
    ok, desc = _call(context, "csv-analyzer-pack", "describe_csv", file_path=file_path)
    billing_stats = desc if ok else {"error": "Could not read billing data"}

    # Step 2: Get column info
    context.next_iteration()
    ok, cols = _call(context, "csv-analyzer-pack", "columns_csv", file_path=file_path)
    columns = cols if ok else {}

    # Step 3: Sample the data
    context.next_iteration()
    ok, head = _call(context, "csv-analyzer-pack", "head_csv", file_path=file_path, n=15)
    sample = head if ok else {}

    # Step 4: Search for cost optimization best practices
    context.next_iteration()
    ok, tips = _call(context, "web-search-pack", "search_web",
                     query="cloud cost optimization best practices 2026",
                     max_results=5)
    optimization_tips = [r.get("title", "") for r in tips.get("results", [])] if ok else []

    # Step 5: Summarize findings
    context.next_iteration()
    analysis_text = f"Billing data: {file_path}\nStats: {billing_stats}\nColumns: {columns}"
    ok, summary = _call(context, "document-summarizer-pack", "document_summary",
                        text=analysis_text, max_sentences=6)

    return {"cost_analysis": summary.get("summary", analysis_text[:500]) if ok else analysis_text[:500],
            "billing_statistics": billing_stats,
            "columns": columns, "sample_data": sample,
            "optimization_tips": optimization_tips,
            "file": file_path, "done": True}
