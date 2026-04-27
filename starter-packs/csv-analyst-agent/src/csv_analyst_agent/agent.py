"""csv_analyst_agent — AgentNode agent v2

CSV Analyst Agent: Upload a CSV, detect patterns and anomalies, and produce an analysis report.
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
    file_path = kwargs.get("file_path", "")
    goal = context.goal
    has_file = bool(file_path and not file_path.startswith("Analyze"))

    # Step 1: Describe the dataset
    context.next_iteration()
    if has_file:
        ok, desc = _call(context, "csv-analyzer-pack", None, file_path=file_path, operation="describe")
        statistics = desc if ok else {"error": "Could not describe dataset"}
    else:
        statistics = {"note": "No CSV file provided — analysis based on goal text"}

    # Step 2: Inspect columns
    context.next_iteration()
    if has_file:
        ok, cols = _call(context, "csv-analyzer-pack", None, file_path=file_path, operation="columns")
        columns = cols if ok else {}
    else:
        columns = {}

    # Step 3: Sample first rows
    context.next_iteration()
    if has_file:
        ok, head = _call(context, "csv-analyzer-pack", None, file_path=file_path, operation="head", n=10)
        sample = head if ok else {}
    else:
        sample = {}

    # Step 4: Summarize findings
    context.next_iteration()
    findings = f"File: {file_path or goal}\nStats: {statistics}\nColumns: {columns}"
    ok, summary = _call(context, "document-summarizer-pack", None,
                        text=findings, max_sentences=6)

    return {"analysis": summary.get("summary", findings[:500]) if ok else findings[:500],
            "statistics": statistics, "columns": columns,
            "sample_data": sample, "file": file_path or goal, "done": True}
