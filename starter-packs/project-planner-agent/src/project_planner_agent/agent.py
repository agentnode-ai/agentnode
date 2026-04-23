"""project_planner_agent — AgentNode agent v3

Project Planner Agent: Break down project goals into user stories, tasks, and milestones, using LLM reasoning.
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
    project = kwargs.get("project", "") or context.goal
    methodology = kwargs.get("methodology", "agile")
    team_size = kwargs.get("team_size", "")

    prompt = (
        f"Create a project plan for: {project}\n\n"
        f"Methodology: {methodology}\n"
    )
    if team_size:
        prompt += f"Team size: {team_size}\n"
    prompt += (
        "\nInclude:\n"
        "1. Scope definition\n"
        "2. User stories\n"
        "3. Task breakdown with effort estimates\n"
        "4. Milestones and timeline\n"
        "5. Risk assessment\n"
        "6. Definition of Done\n\n"
        "Format as a structured markdown document."
    )

    plan = context.call_llm_text([
        {"role": "user", "content": prompt}
    ])

    return {"plan": plan, "project": project, "done": True}
