"""project_planner_agent — AgentNode agent v2

Project Planner Agent: Break down a project goal into user stories, tasks, and milestones.
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
    project = kwargs.get("project", "") or context.goal

    # Step 1: Summarize the project scope
    context.next_iteration()
    ok, summary = _call(context, "document-summarizer-pack", "document_summary",
                        text=project, max_sentences=5)
    scope = summary.get("summary", project[:500]) if ok else project[:500]

    # Step 2: Generate user stories via copywriting framework
    context.next_iteration()
    ok, stories = _call(context, "copywriting-pack", "tone_adjustment",
                        product=f"User stories for: {scope}",
                        audience="development team",
                        framework="aida", tone="technical")
    user_stories = stories.get("copy", stories.get("output", "")) if ok else ""

    # Step 3: Structure the plan
    context.next_iteration()
    plan_text = f"Project: {project}\nScope: {scope}\nStories: {user_stories}"
    ok, plan_summary = _call(context, "document-summarizer-pack", "document_summary",
                             text=plan_text, max_sentences=8)

    # Build structured plan
    plan = f"# Project Plan\n\n## Scope\n{scope}\n\n"
    if user_stories:
        plan += f"## User Stories\n{user_stories}\n\n"
    plan += "## Milestones\n"
    plan += "1. Planning & Setup\n2. Core Implementation\n3. Testing & QA\n4. Deployment\n"

    return {"plan": plan, "scope": scope,
            "user_stories": user_stories,
            "summary": plan_summary.get("summary", "") if ok else "",
            "done": True}
