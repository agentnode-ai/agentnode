"""newsletter_agent — AgentNode agent v2

Newsletter Agent: Curate top stories on a topic, summarize them, and draft a newsletter email.
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
    sender_name = kwargs.get("sender_name", "Newsletter Bot")

    # Step 1: Find top stories
    context.next_iteration()
    ok, search = _call(context, "web-search-pack", "search_web",
                       query=f"{topic} latest news highlights", max_results=8)
    hits = search.get("results", []) if ok else []

    # Step 2: Summarize each story
    stories = []
    for item in hits[:5]:
        url = item.get("url", "")
        title = item.get("title", "")
        if not url:
            continue
        context.next_iteration()
        ok, page = _call(context, "webpage-extractor-pack", "extract_webpage", url=url)
        text = page.get("text", "") if ok else ""

        summary_text = item.get("snippet", "")
        if text:
            ok, summary = _call(context, "document-summarizer-pack", "document_summary",
                                text=text[:2000], max_sentences=2)
            summary_text = summary.get("summary", text[:150]) if ok else text[:150]

        stories.append({"title": title, "url": url, "summary": summary_text})

    # Step 3: Draft the newsletter email
    context.next_iteration()
    stories_text = "\n".join(
        f"- {s['title']}: {s['summary']}" for s in stories
    )
    intent = f"Weekly newsletter about {topic}. Stories:\n{stories_text}"

    ok, email = _call(context, "email-drafter-pack", "email_drafting",
                      intent=intent, tone="friendly", sender_name=sender_name)
    email_body = email.get("email", email.get("output", "")) if ok else ""

    return {"newsletter": email_body, "stories": stories,
            "topic": topic, "story_count": len(stories), "done": True}
