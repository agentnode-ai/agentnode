"""competitive_intel_agent — AgentNode agent v2

Competitive Intelligence Agent: Analyze competitors by scraping web presence, monitoring news, and producing a competitive report.
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
    company = kwargs.get("company", "") or context.goal

    # Step 1: Search for company information
    context.next_iteration()
    ok, search = _call(context, "web-search-pack", None,
                       query=f"{company} company overview products services competitors",
                       max_results=8)
    if not ok:
        return {"error": f"Search failed: {search.get('error')}", "done": False}

    hits = search.get("results", [])
    sources = []
    texts = []

    # Step 2: Extract content from top results
    for item in hits[:4]:
        url = item.get("url", "")
        if not url:
            continue
        context.next_iteration()
        ok, page = _call(context, "webpage-extractor-pack", None, url=url)
        if ok and page.get("text"):
            texts.append(page["text"][:2000])
            sources.append({"title": item.get("title", url), "url": url})

    # Step 3: Search for recent news
    context.next_iteration()
    ok, news_search = _call(context, "web-search-pack", None,
                            query=f"{company} news latest developments 2026",
                            max_results=5)
    news_items = news_search.get("results", []) if ok else []

    # Step 4: Summarize findings
    context.next_iteration()
    combined = "\n\n".join(texts) if texts else f"Company: {company}"
    ok, summary = _call(context, "document-summarizer-pack", None,
                        text=combined, max_sentences=8)
    analysis = summary.get("summary", combined[:800]) if ok else combined[:800]

    return {"analysis": analysis, "company": company, "sources": sources,
            "recent_news": [{"title": n.get("title", ""), "url": n.get("url", "")}
                            for n in news_items],
            "done": True}
