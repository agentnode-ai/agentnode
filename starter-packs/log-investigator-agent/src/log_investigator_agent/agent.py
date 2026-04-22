"""log_investigator_agent — AgentNode agent v2

Log Investigator Agent: Parse log files, identify errors and anomalies, correlate events, and produce a report.
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
    file_path = kwargs.get("file_path", "")
    log_text = kwargs.get("log_text", "") or context.goal

    # Step 1: If file_path given, analyze as CSV (structured logs)
    context.next_iteration()
    log_entries = []
    if file_path:
        ok, desc = _call(context, "csv-analyzer-pack", "describe_csv", file_path=file_path)
        if ok:
            log_entries.append(f"Log statistics: {desc}")
        ok, head = _call(context, "csv-analyzer-pack", "head_csv", file_path=file_path, n=20)
        if ok:
            log_entries.append(f"Recent entries: {head}")

    # Step 2: Process log text as JSON if structured
    context.next_iteration()
    if log_text and (log_text.strip().startswith("[") or log_text.strip().startswith("{")):
        import json
        try:
            data = json.loads(log_text) if isinstance(log_text, str) else log_text
            if isinstance(data, dict):
                data = [data]
            if isinstance(data, list):
                ok, processed = _call(context, "json-processor-pack", "json_processing",
                                      data=data, query="[?level=='ERROR' || level=='error']")
                if ok:
                    log_entries.append(f"Error entries: {processed}")
        except (json.JSONDecodeError, TypeError):
            log_entries.append(log_text[:3000])
    elif log_text:
        log_entries.append(log_text[:3000])

    # Step 3: Search for common error patterns
    context.next_iteration()
    combined = "\n".join(log_entries)
    # Extract error-like lines
    error_lines = [line for line in combined.split("\n")
                   if any(kw in line.lower() for kw in ["error", "exception", "fail", "critical"])]

    if error_lines:
        sample_error = error_lines[0][:100]
        ok, web = _call(context, "web-search-pack", "search_web",
                        query=f"how to fix {sample_error}", max_results=3)
        remediation = [r.get("title", "") for r in web.get("results", [])] if ok else []
    else:
        remediation = []

    # Step 4: Summarize
    context.next_iteration()
    ok, summary = _call(context, "document-summarizer-pack", "document_summary",
                        text=combined[:4000], max_sentences=6)

    return {"findings": summary.get("summary", combined[:500]) if ok else combined[:500],
            "error_count": len(error_lines),
            "sample_errors": error_lines[:5],
            "remediation_hints": remediation,
            "done": True}
