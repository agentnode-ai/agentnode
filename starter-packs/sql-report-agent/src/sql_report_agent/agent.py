"""sql_report_agent — AgentNode agent v2

SQL Report Agent: Answer natural language questions about data by generating SQL queries.
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
    question = kwargs.get("question", "") or context.goal
    schema = kwargs.get("schema", "")
    dialect = kwargs.get("dialect", "postgresql")

    # Step 1: Generate SQL from natural language
    context.next_iteration()
    ok, gen = _call(context, "sql-generator-pack", "generate_sql",
                    description=question, schema=schema, dialect=dialect)
    if not ok:
        return {"error": f"SQL generation failed: {gen.get('error')}", "done": False}

    raw_sql = gen.get("sql", gen.get("output", ""))

    # Step 2: Format the SQL
    context.next_iteration()
    formatted_sql = raw_sql
    if raw_sql:
        ok, fmt = _call(context, "sql-generator-pack", "format_sql",
                        sql=raw_sql, dialect=dialect)
        if ok:
            formatted_sql = fmt.get("formatted_sql", fmt.get("output", raw_sql))

    # Step 3: Summarize what the query does
    context.next_iteration()
    explanation_text = f"Question: {question}\nGenerated SQL: {raw_sql}"
    ok, summary = _call(context, "document-summarizer-pack", "document_summary",
                        text=explanation_text, max_sentences=3)

    return {"question": question, "sql": formatted_sql,
            "explanation": summary.get("summary", "") if ok else "",
            "dialect": dialect, "done": True}
