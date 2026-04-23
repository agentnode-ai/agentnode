"""seo_research_agent — AgentNode agent v2

SEO Research Agent: Audit a website's SEO by analyzing content, keywords, and competitor rankings.
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
    target_url = kwargs.get("url", "") or context.goal
    keyword = kwargs.get("keyword", "")

    # Step 1: Extract target page content
    context.next_iteration()
    ok, page = _call(context, "webpage-extractor-pack", "extract_webpage",
                     url=target_url, include_links=True)
    page_text = page.get("text", "") if ok else ""
    page_title = page.get("title", "") if ok else ""

    # Step 2: Run SEO analysis on the page
    context.next_iteration()
    ok, seo = _call(context, "seo-optimizer-pack", "webpage_extraction",
                    html=page_text, url=target_url, keyword=keyword)
    seo_findings = seo if ok else {}

    # Step 3: Check competitor rankings for the keyword
    context.next_iteration()
    search_query = keyword if keyword else page_title
    ok, competitors = _call(context, "web-search-pack", "search_web",
                            query=search_query, max_results=10)
    competitor_urls = []
    if ok:
        for r in competitors.get("results", []):
            competitor_urls.append({"title": r.get("title", ""), "url": r.get("url", ""),
                                    "snippet": r.get("snippet", "")})

    # Step 4: Summarize findings
    context.next_iteration()
    findings_text = f"Page: {target_url}\nTitle: {page_title}\n"
    if seo_findings:
        findings_text += f"SEO Analysis: {seo_findings}\n"
    findings_text += f"Content length: {len(page_text)} chars"
    ok, summary = _call(context, "document-summarizer-pack", "document_summary",
                        text=findings_text, max_sentences=6)

    return {"url": target_url, "page_title": page_title,
            "seo_analysis": seo_findings, "competitor_rankings": competitor_urls,
            "summary": summary.get("summary", "") if ok else findings_text[:500],
            "content_length": len(page_text), "done": True}
