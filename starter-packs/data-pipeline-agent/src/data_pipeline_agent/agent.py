"""data_pipeline_agent — AgentNode agent v2

Data Pipeline Agent: Build and run a data pipeline: load from CSV/JSON, clean and transform, then output.
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
    filter_column = kwargs.get("filter_column", "")
    filter_value = kwargs.get("filter_value", "")
    filter_operator = kwargs.get("filter_operator", "==")

    # Step 1: Describe the source data
    context.next_iteration()
    ok, desc = _call(context, "csv-analyzer-pack", "describe_csv", file_path=file_path)
    source_stats = desc if ok else {}

    # Step 2: Inspect columns
    context.next_iteration()
    ok, cols = _call(context, "csv-analyzer-pack", "columns_csv", file_path=file_path)
    column_info = cols if ok else {}

    # Step 3: Apply filter if specified
    context.next_iteration()
    filtered_data = None
    if filter_column and filter_value:
        ok, filtered = _call(context, "csv-analyzer-pack", "filter_csv",
                             file_path=file_path, column=filter_column,
                             value=filter_value, operator=filter_operator)
        if ok:
            filtered_data = filtered

    # Step 4: Get processed data sample
    context.next_iteration()
    ok, head = _call(context, "csv-analyzer-pack", "head_csv", file_path=file_path, n=20)
    sample = head if ok else {}

    records_note = "all records"
    if filtered_data:
        records_note = f"filtered by {filter_column} {filter_operator} {filter_value}"

    return {"source_file": file_path, "source_stats": source_stats,
            "columns": column_info, "filter_applied": records_note,
            "filtered_data": filtered_data, "sample": sample,
            "done": True}
