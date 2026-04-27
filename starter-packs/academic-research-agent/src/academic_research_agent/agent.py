"""academic_research_agent — AgentNode agent v2

Academic Research Agent: Search academic papers on arXiv, extract content, and produce a literature review.
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

    # Step 1: Search for academic papers
    context.next_iteration()
    ok, search = _call(context, "web-search-pack", None,
                       query=f"site:arxiv.org OR site:scholar.google.com {topic}",
                       max_results=10)
    if not ok:
        return {"error": f"Search failed: {search.get('error')}", "done": False}

    hits = search.get("results", [])
    papers = []
    texts = []

    # Step 2: Extract content — try PDF for arxiv, webpage for others
    for item in hits[:5]:
        url = item.get("url", "")
        if not url:
            continue
        context.next_iteration()

        if "arxiv.org" in url and "/abs/" in url:
            pdf_url = url.replace("/abs/", "/pdf/") + ".pdf"
            ok, pdf = _call(context, "pdf-extractor-pack", None,
                            file_path=pdf_url, pages="1-5")
            if ok and pdf.get("text"):
                texts.append(pdf["text"][:3000])
                papers.append({"title": item.get("title", ""), "url": url, "type": "pdf"})
                continue

        ok, page = _call(context, "webpage-extractor-pack", None, url=url)
        if ok and page.get("text"):
            texts.append(page["text"][:3000])
            papers.append({"title": item.get("title", url), "url": url, "type": "webpage"})

    if not texts:
        return {"review": "No academic content found.", "papers": [], "done": True}

    # Step 3: Summarize into a literature review
    context.next_iteration()
    combined = "\n\n---\n\n".join(texts)
    ok, summary = _call(context, "document-summarizer-pack", None,
                        text=combined, max_sentences=12)

    review = summary.get("summary", combined[:1000]) if ok else combined[:1000]

    return {"review": review, "papers": papers, "topic": topic,
            "sources_found": len(papers), "done": True}
