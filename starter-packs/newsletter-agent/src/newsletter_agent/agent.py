"""newsletter_agent — AgentNode agent v3

Newsletter Agent: Draft engaging newsletter emails on any topic, using LLM reasoning.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def run(context: Any, **kwargs: Any) -> dict:
    """Agent entrypoint — LLM-only agent (tier: llm_only).

    Uses context.call_llm_text() for LLM reasoning.
    System prompt is injected automatically from the manifest.

    Args:
        context: AgentContext with goal and LLM/tool access.
        **kwargs: Additional parameters from the caller.

    Returns:
        Structured result dict.
    """
    topic = kwargs.get("topic", "") or context.goal
    sender_name = kwargs.get("sender_name", "Newsletter Bot")
    audience = kwargs.get("audience", "subscribers")

    prompt = (
        f"Write a newsletter email about: {topic}\n\n"
        f"Sender name: {sender_name}\n"
        f"Target audience: {audience}\n\n"
        "Include:\n"
        "1. A catchy subject line\n"
        "2. An opening hook\n"
        "3. 3-5 story sections with headlines and brief descriptions\n"
        "4. A closing with call-to-action\n\n"
        "Write based on your knowledge of the topic. Be specific and current."
    )

    newsletter = context.call_llm_text([
        {"role": "user", "content": prompt}
    ])

    return {"newsletter": newsletter, "topic": topic, "done": True}
