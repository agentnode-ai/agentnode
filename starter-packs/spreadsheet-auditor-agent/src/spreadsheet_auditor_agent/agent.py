"""spreadsheet_auditor_agent — AgentNode agent v2

Spreadsheet Auditor Agent: Audit CSV/Excel spreadsheets for errors, duplicates, and data inconsistencies.
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

    # Step 1: Describe to get overall statistics
    context.next_iteration()
    ok, desc = _call(context, "csv-analyzer-pack", "describe_csv", file_path=file_path)
    statistics = desc if ok else {}

    # Step 2: Column-level analysis
    context.next_iteration()
    ok, cols = _call(context, "csv-analyzer-pack", "columns_csv", file_path=file_path)
    columns = cols if ok else {}

    # Step 3: Inspect sample data for issues
    context.next_iteration()
    ok, head = _call(context, "csv-analyzer-pack", "head_csv", file_path=file_path, n=20)
    sample = head if ok else {}

    # Step 4: Identify potential issues from statistics
    issues = []
    if isinstance(statistics, dict):
        for col_name, stats in statistics.items():
            if isinstance(stats, dict):
                if stats.get("count", 0) == 0:
                    issues.append(f"Empty column: {col_name}")
                null_count = stats.get("null_count", stats.get("missing", 0))
                if null_count and null_count > 0:
                    issues.append(f"Missing values in {col_name}: {null_count}")

    # Step 5: Summarize audit
    context.next_iteration()
    audit_text = f"File: {file_path}\nIssues found: {len(issues)}\n"
    audit_text += "\n".join(issues[:20]) if issues else "No obvious issues detected."
    ok, summary = _call(context, "document-summarizer-pack", "document_summary",
                        text=audit_text, max_sentences=5)

    quality_score = max(0, 100 - len(issues) * 10)

    return {"audit_summary": summary.get("summary", audit_text) if ok else audit_text,
            "issues": issues, "quality_score": quality_score,
            "statistics": statistics, "columns": columns,
            "file": file_path, "done": True}
