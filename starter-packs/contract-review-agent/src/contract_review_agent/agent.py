"""contract_review_agent — AgentNode agent v2

Contract Review Agent: Analyze legal contracts, flag risky clauses, compare against templates.
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
    text = kwargs.get("text", "")
    file_path = kwargs.get("file_path", "")

    # Step 1: Extract text from PDF if file provided
    context.next_iteration()
    contract_text = text
    if file_path and not text:
        ok, pdf = _call(context, "pdf-extractor-pack", "pdf_extraction",
                        file_path=file_path, extract_tables=True)
        if ok:
            contract_text = pdf.get("text", "")

    if not contract_text:
        contract_text = context.goal

    # Step 2: Analyze contract for risks
    context.next_iteration()
    ok, review = _call(context, "contract-review-pack", "document_parsing",
                       text=contract_text[:5000], check_risks=True, extract_terms=True)
    contract_analysis = review if ok else {}

    # Step 3: Summarize the contract
    context.next_iteration()
    ok, summary = _call(context, "document-summarizer-pack", "document_summary",
                        text=contract_text[:5000], max_sentences=8)
    contract_summary = summary.get("summary", contract_text[:500]) if ok else contract_text[:500]

    return {"summary": contract_summary,
            "risk_analysis": contract_analysis.get("risks", contract_analysis),
            "key_terms": contract_analysis.get("terms", {}),
            "text_length": len(contract_text),
            "done": True}
