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
    file_path = kwargs.get("file_path", "") or context.goal

    # Step 1: Describe the dataset
    context.next_iteration()
    ok, desc = _call(context, "csv-analyzer-pack", "describe_csv", file_path=file_path)
    statistics = desc if ok else {"error": "Could not describe dataset"}

    # Step 2: Inspect columns
    context.next_iteration()
    ok, cols = _call(context, "csv-analyzer-pack", "columns_csv", file_path=file_path)
    columns = cols if ok else {}

    # Step 3: Sample first rows
    context.next_iteration()
    ok, head = _call(context, "csv-analyzer-pack", "head_csv", file_path=file_path, n=10)
    sample = head if ok else {}

    # Step 4: Summarize findings
    context.next_iteration()
    findings = f"File: {file_path}\nStats: {statistics}\nColumns: {columns}"
    ok, summary = _call(context, "document-summarizer-pack", "document_summary",
                        text=findings, max_sentences=6)

    return {"analysis": summary.get("summary", findings[:500]) if ok else findings[:500],
            "statistics": statistics, "columns": columns,
            "sample_data": sample, "file": file_path, "done": True}
