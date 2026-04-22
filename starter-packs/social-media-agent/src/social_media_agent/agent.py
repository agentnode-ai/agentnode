"""social_media_agent — AgentNode agent v2

Social Media Agent: Create platform-optimized social media posts with copy, hashtags, and suggestions.
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
    topic = kwargs.get("topic", "") or context.goal
    url = kwargs.get("url", "")

    # Step 1: Research or extract content
    context.next_iteration()
    source_text = ""
    if url:
        ok, page = _call(context, "webpage-extractor-pack", "extract_webpage", url=url)
        source_text = page.get("text", "")[:2000] if ok else ""
    else:
        ok, search = _call(context, "web-search-pack", "search_web",
                           query=f"{topic} trending", max_results=5)
        if ok:
            snippets = [r.get("snippet", "") for r in search.get("results", [])]
            source_text = " ".join(snippets)[:2000]

    # Step 2: Summarize source material
    context.next_iteration()
    ok, summary = _call(context, "document-summarizer-pack", "document_summary",
                        text=source_text or topic, max_sentences=3)
    key_message = summary.get("summary", source_text[:200]) if ok else source_text[:200]

    # Step 3: Generate copy for each platform
    posts = {}
    for platform, tone, max_len in [
        ("twitter", "witty", 280),
        ("linkedin", "professional", 600),
        ("instagram", "casual", 400),
    ]:
        context.next_iteration()
        ok, copy = _call(context, "copywriting-pack", "tone_adjustment",
                         product=f"{platform} post about: {key_message[:100]}",
                         audience=f"{platform} users", tone=tone)
        text = copy.get("copy", copy.get("output", key_message)) if ok else key_message
        posts[platform] = text[:max_len] if isinstance(text, str) else str(text)[:max_len]

    return {"posts": posts, "key_message": key_message, "topic": topic, "done": True}
