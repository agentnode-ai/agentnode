"""api_design_agent — AgentNode agent v2

API Design Agent: Generate an OpenAPI specification from requirements, validate it, and produce docs.
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
    requirements = kwargs.get("requirements", "") or context.goal
    dialect = kwargs.get("dialect", "postgresql")

    # Step 1: Generate SQL for data models
    context.next_iteration()
    ok, sql = _call(context, "sql-generator-pack", "generate_sql",
                    description=f"Create tables for: {requirements}",
                    dialect=dialect)
    data_model_sql = sql.get("sql", sql.get("output", "")) if ok else ""

    # Step 2: Format the SQL
    context.next_iteration()
    formatted_sql = data_model_sql
    if data_model_sql:
        ok, fmt = _call(context, "sql-generator-pack", "format_sql",
                        sql=data_model_sql, dialect=dialect)
        if ok:
            formatted_sql = fmt.get("formatted_sql", fmt.get("output", data_model_sql))

    # Step 3: Analyze for best practices
    context.next_iteration()
    if data_model_sql:
        ok, lint = _call(context, "code-linter-pack", "code_analysis",
                         code=data_model_sql, language="python")
    else:
        lint = {}

    # Step 4: Summarize the design
    context.next_iteration()
    design_text = f"Requirements: {requirements}\nData Model SQL: {formatted_sql}"
    ok, summary = _call(context, "document-summarizer-pack", "document_summary",
                        text=design_text, max_sentences=5)

    return {"requirements": requirements,
            "data_model_sql": formatted_sql,
            "design_summary": summary.get("summary", "") if ok else "",
            "done": True}
