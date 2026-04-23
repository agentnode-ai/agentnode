"""deep_research_agent — AgentNode agent v2

Deep Research Agent: Conduct deep multi-source research on any topic, synthesize findings into a structured report.
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
    topic = kwargs.get("topic", "") or context.goal

    # Step 1: Search the web
    context.next_iteration()
    ok, search = _call(context, "web-search-pack", "search_web",
                       query=topic, max_results=10)
    if not ok:
        return {"error": f"Search failed: {search.get('error')}", "done": False}

    hits = search.get("results", [])
    sources = []
    texts = []

    # Step 2: Extract content from top results
    for item in hits[:5]:
        url = item.get("url", "")
        if not url:
            continue
        context.next_iteration()
        ok, page = _call(context, "webpage-extractor-pack", "extract_webpage", url=url)
        if ok and page.get("text"):
            texts.append(page["text"][:3000])
            sources.append({"title": item.get("title", url), "url": url})

    if not texts:
        return {"report": "No content could be extracted from search results.",
                "search_results": [{"title": h.get("title", ""), "url": h.get("url", "")} for h in hits],
                "done": True}

    # Step 3: Summarize combined content
    context.next_iteration()
    combined = "\n\n---\n\n".join(texts)
    ok, summary = _call(context, "document-summarizer-pack", "document_summary",
                        text=combined, max_sentences=10)

    report = summary.get("summary", combined[:1000]) if ok else combined[:1000]

    return {"report": report, "sources": sources, "topic": topic,
            "pages_analyzed": len(texts), "done": True}
