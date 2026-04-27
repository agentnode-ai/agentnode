"""crm_enrichment_agent — AgentNode agent v2

CRM Enrichment Agent: Enrich CRM contacts with web data: company info, social profiles, and news.
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
    contact = kwargs.get("contact", "") or context.goal
    company = kwargs.get("company", "")

    # Step 1: Search for the contact/person
    context.next_iteration()
    ok, person_search = _call(context, "web-search-pack", None,
                              query=f"{contact} professional profile linkedin",
                              max_results=5)
    person_results = person_search.get("results", []) if ok else []

    # Step 2: Search for company info
    context.next_iteration()
    company_info = {}
    if company:
        ok, company_search = _call(context, "web-search-pack", None,
                                   query=f"{company} company about products",
                                   max_results=5)
        if ok:
            company_info = {"results": company_search.get("results", [])}

    # Step 3: Extract details from top results
    profile_texts = []
    for item in person_results[:3]:
        url = item.get("url", "")
        if not url:
            continue
        context.next_iteration()
        ok, page = _call(context, "webpage-extractor-pack", None, url=url)
        if ok and page.get("text"):
            profile_texts.append(page["text"][:1500])

    # Step 4: Search for recent news
    context.next_iteration()
    search_name = f"{contact} {company}" if company else contact
    ok, news = _call(context, "web-search-pack", None,
                     query=f"{search_name} news recent", max_results=5)
    news_items = news.get("results", []) if ok else []

    # Step 5: Summarize profile
    context.next_iteration()
    combined = "\n".join(profile_texts) if profile_texts else contact
    ok, summary = _call(context, "document-summarizer-pack", None,
                        text=combined, max_sentences=5)

    return {"contact": contact, "company": company,
            "profile_summary": summary.get("summary", "") if ok else "",
            "social_links": [r.get("url", "") for r in person_results[:3]],
            "company_info": company_info,
            "recent_news": [{"title": n.get("title", ""), "url": n.get("url", "")}
                            for n in news_items],
            "done": True}
