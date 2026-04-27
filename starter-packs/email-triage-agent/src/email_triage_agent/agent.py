"""email_triage_agent — AgentNode agent v2

Email Triage Agent: Prioritize incoming emails, draft responses for routine messages, extract action items.
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
    emails_text = kwargs.get("emails", "") or context.goal

    # Step 1: Summarize the emails
    context.next_iteration()
    ok, summary = _call(context, "document-summarizer-pack", None,
                        text=emails_text[:5000], max_sentences=10)
    email_summary = summary.get("summary", emails_text[:500]) if ok else emails_text[:500]

    # Step 2: Draft response for the email
    context.next_iteration()
    ok, response = _call(context, "email-drafter-pack", None,
                         intent=f"Reply to: {email_summary[:500]}",
                         tone="professional")
    draft = response.get("email", response.get("output", "")) if ok else ""

    # Step 3: Extract action items by summarizing again with focus
    context.next_iteration()
    ok, actions = _call(context, "document-summarizer-pack", None,
                        text=f"Extract action items from: {emails_text[:3000]}",
                        max_sentences=5)

    # Simple priority classification based on keywords
    text_lower = emails_text.lower()
    if any(w in text_lower for w in ["urgent", "asap", "critical", "deadline"]):
        priority = "high"
    elif any(w in text_lower for w in ["important", "please review", "action required"]):
        priority = "medium"
    else:
        priority = "low"

    return {"summary": email_summary, "priority": priority,
            "draft_response": draft,
            "action_items": actions.get("summary", "") if ok else "",
            "done": True}
