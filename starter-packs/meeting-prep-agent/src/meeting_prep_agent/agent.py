"""meeting_prep_agent — AgentNode agent v2

Meeting Prep Agent: Prepare for meetings by researching attendees, summarizing docs, and generating an agenda.
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
    attendees = kwargs.get("attendees", "")

    # Step 1: Research the meeting topic
    context.next_iteration()
    ok, topic_search = _call(context, "web-search-pack", "search_web",
                             query=topic, max_results=5)
    topic_results = topic_search.get("results", []) if ok else []

    # Step 2: Research attendees if provided
    attendee_info = []
    if attendees:
        for person in attendees.split(",")[:3]:
            person = person.strip()
            if not person:
                continue
            context.next_iteration()
            ok, search = _call(context, "web-search-pack", "search_web",
                               query=f"{person} professional background", max_results=3)
            if ok:
                snippets = [r.get("snippet", "") for r in search.get("results", [])]
                attendee_info.append({"name": person, "background": " ".join(snippets)[:300]})

    # Step 3: Extract key content from topic results
    context.next_iteration()
    topic_texts = []
    for item in topic_results[:3]:
        url = item.get("url", "")
        if url:
            ok, page = _call(context, "webpage-extractor-pack", "extract_webpage", url=url)
            if ok and page.get("text"):
                topic_texts.append(page["text"][:1500])

    # Step 4: Summarize into agenda
    context.next_iteration()
    prep_text = f"Meeting topic: {topic}\n"
    if topic_texts:
        prep_text += "Background: " + "\n".join(topic_texts)[:2000]
    ok, summary = _call(context, "document-summarizer-pack", "document_summary",
                        text=prep_text, max_sentences=8)

    agenda = f"# Meeting: {topic}\n\n"
    agenda += "## Key Points\n"
    agenda += (summary.get("summary", "") if ok else prep_text[:500]) + "\n\n"
    if attendee_info:
        agenda += "## Attendees\n"
        for a in attendee_info:
            agenda += f"- **{a['name']}**: {a['background']}\n"

    return {"agenda": agenda, "topic": topic,
            "attendee_research": attendee_info,
            "background_sources": [r.get("url", "") for r in topic_results],
            "done": True}
