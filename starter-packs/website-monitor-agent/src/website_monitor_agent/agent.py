"""website_monitor_agent — AgentNode agent v2

Website Monitor Agent: Monitor websites for content changes, downtime, and extract current state.
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
    url = kwargs.get("url", "") or context.goal
    previous_snapshot = kwargs.get("previous_text", "")

    # Step 1: Extract current page content
    context.next_iteration()
    ok, page = _call(context, "webpage-extractor-pack", None,
                     url=url)
    if not ok:
        return {"url": url, "status": "down",
                "error": page.get("error", "Could not reach site"), "done": True}

    current_text = page.get("text", "")
    current_title = page.get("title", "")

    # Step 2: Check if content changed vs previous snapshot
    changes_detected = False
    if previous_snapshot:
        changes_detected = current_text.strip() != previous_snapshot.strip()

    # Step 3: Summarize current content
    context.next_iteration()
    ok, summary = _call(context, "document-summarizer-pack", None,
                        text=current_text[:3000], max_sentences=5)
    content_summary = summary.get("summary", current_text[:300]) if ok else current_text[:300]

    # Step 4: Check for common issues via search
    context.next_iteration()
    ok, uptime = _call(context, "web-search-pack", None,
                       query=f"is {url} down today", max_results=3)
    uptime_reports = [r.get("title", "") for r in uptime.get("results", [])] if ok else []

    return {"url": url, "status": "up", "title": current_title,
            "content_summary": content_summary,
            "content_length": len(current_text),
            "changes_detected": changes_detected,
            "uptime_reports": uptime_reports,
            "snapshot": current_text[:5000],
            "done": True}
