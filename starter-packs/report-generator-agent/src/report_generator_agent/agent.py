"""report_generator_agent — AgentNode agent v2

Report Generator Agent: Transform raw data (CSV, JSON) into a formatted report with statistics and summary.
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
    file_path = kwargs.get("file_path", "") or context.goal

    # Step 1: Get dataset description
    context.next_iteration()
    ok, desc = _call(context, "csv-analyzer-pack", "describe_csv", file_path=file_path)
    statistics = desc if ok else {}

    # Step 2: Get column information
    context.next_iteration()
    ok, cols = _call(context, "csv-analyzer-pack", "columns_csv", file_path=file_path)
    column_info = cols if ok else {}

    # Step 3: Get sample data
    context.next_iteration()
    ok, head = _call(context, "csv-analyzer-pack", "head_csv", file_path=file_path, n=5)
    sample_data = head if ok else {}

    # Step 4: Generate executive summary
    context.next_iteration()
    report_text = f"Dataset: {file_path}\n"
    if statistics:
        report_text += f"Statistics: {statistics}\n"
    if column_info:
        report_text += f"Columns: {column_info}\n"
    ok, summary = _call(context, "document-summarizer-pack", "document_summary",
                        text=report_text, max_sentences=5)
    exec_summary = summary.get("summary", report_text[:500]) if ok else report_text[:500]

    return {"executive_summary": exec_summary, "statistics": statistics,
            "column_info": column_info, "sample_data": sample_data,
            "file": file_path, "done": True}
