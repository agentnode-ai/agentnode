"""Entry-point tool for the Research Agent pack.

This module exposes the ``run()`` function expected by the AgentNode
tool-runner convention.  It instantiates the :class:`ResearchAgent`,
executes the research workflow, and returns a structured report.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from research_agent_pack.agent import ResearchAgent

logger = logging.getLogger(__name__)


def run(query: str, pdf_path: str = "", max_sources: int = 5) -> dict[str, Any]:
    """Conduct multi-source research on a topic and return a structured report.

    This is the AgentNode tool entry point.  It orchestrates three
    starter packs:

    * **web-search-pack** — searches the web via DuckDuckGo
    * **pdf-reader-pack** — extracts text/tables from a PDF (optional)
    * **document-summarizer-pack** — produces an extractive summary

    Args:
        query: The research topic or question.
        pdf_path: Optional filesystem path to a PDF file to include in
            the research.  Pass an empty string or omit to skip PDF
            analysis.
        max_sources: Maximum number of web search results to retrieve
            (1-20, default 5).

    Returns:
        A dictionary with the following keys:

        * ``query`` — the original query string.
        * ``sources`` — list of source dicts (title, url, snippet,
          source_type).
        * ``findings`` — list of finding dicts (source, content).
        * ``summary`` — extractive summary of all findings.
        * ``metadata`` — run metadata (timestamp, counts, capability
          resolution results).

    Example::

        >>> from research_agent_pack.tool import run
        >>> result = run("quantum computing applications")
        >>> print(result["summary"])
    """
    # Clamp max_sources to valid range
    max_sources = max(1, min(max_sources, 20))

    # Resolve the optional PDF path (empty string -> None)
    effective_pdf: str | None = pdf_path.strip() if pdf_path else None
    if effective_pdf and not os.path.isfile(effective_pdf):
        logger.warning(
            "Provided pdf_path does not exist: '%s'. Skipping PDF analysis.",
            effective_pdf,
        )
        effective_pdf = None

    # Instantiate the agent (picks up AGENTNODE_API_KEY from env if set)
    agent = ResearchAgent()

    # Run the full research workflow
    report = agent.research(
        query=query,
        pdf_path=effective_pdf,
        max_sources=max_sources,
    )

    return report
