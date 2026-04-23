"""report_generator_agent — AgentNode agent v3

Report Generator Agent: Generate structured business reports with executive summary from provided data, using LLM reasoning.
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
    data = kwargs.get("data", "") or kwargs.get("text", "") or context.goal
    report_type = kwargs.get("report_type", "business analysis")
    audience = kwargs.get("audience", "stakeholders")

    prompt = (
        f"Generate a {report_type} report based on the following information:\n\n"
        f"{data}\n\n"
        f"Target audience: {audience}\n\n"
        "Include:\n"
        "1. Executive summary with key takeaways\n"
        "2. Detailed analysis\n"
        "3. Key findings and insights\n"
        "4. Recommendations\n"
        "5. Conclusion\n\n"
        "Format as a professional markdown report."
    )

    report = context.call_llm_text([
        {"role": "user", "content": prompt}
    ])

    return {"report": report, "report_type": report_type, "done": True}
