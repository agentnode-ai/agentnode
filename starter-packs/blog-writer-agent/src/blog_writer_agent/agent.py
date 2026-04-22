"""blog_writer_agent — AgentNode agent v2

Blog Writer Agent: Research a topic and write an SEO-optimized blog post with structure and keywords.
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
    audience = kwargs.get("audience", "general readers")

    # Step 1: Research the topic
    context.next_iteration()
    ok, search = _call(context, "web-search-pack", "search_web",
                       query=topic, max_results=8)
    hits = search.get("results", []) if ok else []

    # Step 2: Extract top articles for reference
    reference_texts = []
    sources = []
    for item in hits[:3]:
        url = item.get("url", "")
        if not url:
            continue
        context.next_iteration()
        ok, page = _call(context, "webpage-extractor-pack", "extract_webpage", url=url)
        if ok and page.get("text"):
            reference_texts.append(page["text"][:2000])
            sources.append({"title": item.get("title", ""), "url": url})

    # Step 3: Summarize reference material
    context.next_iteration()
    combined = "\n\n".join(reference_texts) if reference_texts else topic
    ok, summary = _call(context, "document-summarizer-pack", "document_summary",
                        text=combined, max_sentences=8)
    key_points = summary.get("summary", combined[:500]) if ok else combined[:500]

    # Step 4: Generate blog copy
    context.next_iteration()
    ok, copy = _call(context, "copywriting-pack", "tone_adjustment",
                     product=f"Blog post about: {topic}",
                     audience=audience, framework="aida", tone="informative")
    blog_body = copy.get("copy", copy.get("output", "")) if ok else ""

    # Assemble the blog post
    blog_post = f"# {topic}\n\n"
    if blog_body:
        blog_post += blog_body + "\n\n"
    blog_post += f"## Key Points\n\n{key_points}\n\n"
    if sources:
        blog_post += "## Sources\n\n"
        for s in sources:
            blog_post += f"- [{s['title']}]({s['url']})\n"

    return {"article": blog_post, "title": topic,
            "key_points": key_points, "sources": sources,
            "done": True}
