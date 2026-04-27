"""fact_check_agent — AgentNode agent v2

Fact Check Agent: Verify claims against multiple web sources and produce a fact-check verdict with evidence.
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
    claim = kwargs.get("claim", "") or context.goal

    # Step 1: Search for supporting evidence
    context.next_iteration()
    ok, support_search = _call(context, "web-search-pack", None,
                               query=f"evidence supporting \"{claim}\"",
                               max_results=5)
    supporting = support_search.get("results", []) if ok else []

    # Step 2: Search for contradicting evidence
    context.next_iteration()
    ok, contra_search = _call(context, "web-search-pack", None,
                              query=f"evidence against debunk \"{claim}\"",
                              max_results=5)
    contradicting = contra_search.get("results", []) if ok else []

    # Step 3: Extract content from top sources
    all_sources = []
    support_texts = []
    contra_texts = []

    for item in supporting[:3]:
        url = item.get("url", "")
        if not url:
            continue
        context.next_iteration()
        ok, page = _call(context, "webpage-extractor-pack", None, url=url)
        if ok and page.get("text"):
            support_texts.append(page["text"][:1500])
            all_sources.append({"title": item.get("title", ""), "url": url, "stance": "supporting"})

    for item in contradicting[:3]:
        url = item.get("url", "")
        if not url:
            continue
        context.next_iteration()
        ok, page = _call(context, "webpage-extractor-pack", None, url=url)
        if ok and page.get("text"):
            contra_texts.append(page["text"][:1500])
            all_sources.append({"title": item.get("title", ""), "url": url, "stance": "contradicting"})

    # Step 4: Synthesize verdict
    support_count = len(support_texts)
    contra_count = len(contra_texts)
    total = support_count + contra_count

    if total == 0:
        verdict = "unverifiable"
        confidence = 0.0
    elif contra_count == 0:
        verdict = "likely_true"
        confidence = min(0.9, 0.5 + support_count * 0.15)
    elif support_count == 0:
        verdict = "likely_false"
        confidence = min(0.9, 0.5 + contra_count * 0.15)
    else:
        ratio = support_count / total
        if ratio > 0.7:
            verdict = "likely_true"
        elif ratio < 0.3:
            verdict = "likely_false"
        else:
            verdict = "disputed"
        confidence = round(abs(ratio - 0.5) * 2, 2)

    return {"claim": claim, "verdict": verdict, "confidence": confidence,
            "supporting_sources": support_count, "contradicting_sources": contra_count,
            "sources": all_sources, "done": True}
