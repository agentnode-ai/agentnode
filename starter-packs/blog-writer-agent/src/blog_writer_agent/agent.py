"""blog_writer_agent — AgentNode agent v3

Blog Writer Agent: Write SEO-optimized blog posts with compelling structure, using LLM reasoning.
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
    audience = kwargs.get("audience", "general readers")
    tone = kwargs.get("tone", "professional but approachable")

    prompt = (
        f"Write a comprehensive blog post about: {topic}\n\n"
        f"Target audience: {audience}\n"
        f"Tone: {tone}\n\n"
        "Include:\n"
        "1. A compelling headline\n"
        "2. An engaging introduction\n"
        "3. 3-5 main sections with H2 headers\n"
        "4. Actionable takeaways\n"
        "5. A conclusion with call-to-action\n"
        "6. A suggested meta description (1 sentence)\n\n"
        "Format as markdown."
    )

    article = context.call_llm_text([
        {"role": "user", "content": prompt}
    ])

    return {"article": article, "title": topic, "done": True}
