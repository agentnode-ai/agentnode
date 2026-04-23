"""social_media_agent — AgentNode agent v3

Social Media Agent: Create platform-optimized social media posts with copy and hashtags, using LLM reasoning.
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
    import json as _json

    topic = kwargs.get("topic", "") or context.goal
    platforms = kwargs.get("platforms", "twitter,linkedin,instagram")

    prompt = (
        f"Create social media posts about: {topic}\n\n"
        f"Platforms: {platforms}\n\n"
        "For each platform, write an optimized post.\n"
        "Respond with a JSON object like:\n"
        '{\n'
        '  "key_message": "one-sentence summary",\n'
        '  "posts": {\n'
        '    "twitter": "tweet text with #hashtags",\n'
        '    "linkedin": "professional post...",\n'
        '    "instagram": "caption with #hashtags"\n'
        '  }\n'
        '}\n'
        "Only output the JSON, no other text."
    )

    raw = context.call_llm_text([
        {"role": "user", "content": prompt}
    ])

    # Parse JSON response, fall back to raw text
    try:
        data = _json.loads(raw)
        posts = data.get("posts", {})
        key_message = data.get("key_message", topic)
    except (_json.JSONDecodeError, TypeError):
        posts = {"raw": raw}
        key_message = topic

    return {"posts": posts, "key_message": key_message,
            "topic": topic, "done": True}
