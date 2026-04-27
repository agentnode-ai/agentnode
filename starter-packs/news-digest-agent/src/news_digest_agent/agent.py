"""news_digest_agent — AgentNode agent v2

News Digest Agent: Aggregate news from multiple sources on a topic, summarize stories, and optionally translate.
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
    target_language = kwargs.get("target_language", "")

    # Step 1: Aggregate news
    context.next_iteration()
    ok, news = _call(context, "news-aggregator-pack", None,
                     topic=topic, limit=10)
    articles_raw = news.get("results", news.get("articles", [])) if ok else []

    # Fallback to web search if aggregator returns nothing
    if not articles_raw:
        context.next_iteration()
        ok, search = _call(context, "web-search-pack", None,
                           query=f"{topic} news latest", max_results=10)
        articles_raw = search.get("results", []) if ok else []

    # Step 2: Extract and summarize each article
    digest_items = []
    for item in articles_raw[:6]:
        url = item.get("url", item.get("link", ""))
        title = item.get("title", "")
        if not url:
            continue
        context.next_iteration()
        ok, page = _call(context, "webpage-extractor-pack", None, url=url)
        text = page.get("text", "") if ok else ""

        summary_text = ""
        if text:
            ok, summary = _call(context, "document-summarizer-pack", None,
                                text=text[:3000], max_sentences=3)
            summary_text = summary.get("summary", text[:200]) if ok else text[:200]

        digest_items.append({"title": title, "url": url, "summary": summary_text})

    # Step 3: Optional translation
    if target_language and digest_items:
        context.next_iteration()
        full_digest = "\n\n".join(
            f"## {d['title']}\n{d['summary']}" for d in digest_items
        )
        ok, translated = _call(context, "text-translator-pack", None,
                               text=full_digest, target_language=target_language)
        if ok:
            return {"digest": translated.get("translated_text", translated.get("output", full_digest)),
                    "articles": digest_items, "topic": topic,
                    "language": target_language, "done": True}

    return {"digest": digest_items, "topic": topic,
            "article_count": len(digest_items), "done": True}
