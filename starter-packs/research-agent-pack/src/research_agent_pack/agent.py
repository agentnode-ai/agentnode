"""Research Agent — orchestrates web-search, pdf-reader, and document-summarizer packs.

Demonstrates the AgentNode core flow:
  Capability Resolution -> Installation -> Integration

The agent coordinates three MVP starter packs to conduct multi-source
research and produce a structured report.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class ResearchAgent:
    """Orchestrates web search, PDF extraction, and summarization into a
    structured research report.

    Args:
        api_key: Optional AgentNode API key for capability resolution.
            Falls back to the ``AGENTNODE_API_KEY`` environment variable.
    """

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("AGENTNODE_API_KEY", "")
        self._resolved_capabilities: dict[str, bool] = {}

    # ------------------------------------------------------------------
    # Capability Resolution (demonstrates the AgentNode core flow)
    # ------------------------------------------------------------------

    def resolve_capabilities(self) -> dict[str, bool]:
        """Resolve required capabilities via the AgentNode API.

        Uses the SDK to verify that the three dependent packs are available
        and compatible.  If no API key is configured the step is skipped
        gracefully — the packs are assumed to be locally installed.

        Returns:
            Mapping of capability_id -> resolved (bool).
        """
        capabilities = {
            "web_search": False,
            "pdf_extraction": False,
            "document_summary": False,
        }

        if not self._api_key:
            logger.info(
                "No AgentNode API key configured — skipping capability "
                "resolution, assuming local pack installation."
            )
            # Probe local availability instead
            capabilities["web_search"] = self._probe_local("web_search_pack")
            capabilities["pdf_extraction"] = self._probe_local("pdf_reader_pack")
            capabilities["document_summary"] = self._probe_local(
                "document_summarizer_pack"
            )
            self._resolved_capabilities = capabilities
            return capabilities

        try:
            from agentnode_sdk import AgentNodeClient

            client = AgentNodeClient(api_key=self._api_key)
            try:
                result = client.resolve(
                    capabilities=["web_search", "pdf_extraction", "document_summary"],
                    runtime="python",
                )
                resolved_slugs = {r.slug for r in result.results}
                capabilities["web_search"] = "web-search-pack" in resolved_slugs
                capabilities["pdf_extraction"] = "pdf-reader-pack" in resolved_slugs
                capabilities["document_summary"] = (
                    "document-summarizer-pack" in resolved_slugs
                )
                logger.info(
                    "Capability resolution complete: %s", capabilities
                )
            finally:
                client.close()
        except Exception as exc:
            logger.warning(
                "Capability resolution via API failed (%s). "
                "Falling back to local probe.",
                exc,
            )
            capabilities["web_search"] = self._probe_local("web_search_pack")
            capabilities["pdf_extraction"] = self._probe_local("pdf_reader_pack")
            capabilities["document_summary"] = self._probe_local(
                "document_summarizer_pack"
            )

        self._resolved_capabilities = capabilities
        return capabilities

    # ------------------------------------------------------------------
    # Core research workflow
    # ------------------------------------------------------------------

    def research(self, query: str, pdf_path: str | None = None,
                 max_sources: int = 5) -> dict[str, Any]:
        """Run a full research workflow on *query*.

        1. Resolve capabilities (if not already done).
        2. Search the web for the query.
        3. Optionally extract text from a PDF.
        4. Collect all findings.
        5. Summarize the combined findings.
        6. Return a structured report.

        Args:
            query: The research topic or question.
            pdf_path: Optional filesystem path to a PDF to include.
            max_sources: Maximum number of web results to retrieve (1-20).

        Returns:
            A dict with keys: query, sources, findings, summary, metadata.
        """
        if not query or not query.strip():
            raise ValueError("A non-empty research query is required.")

        # Step 1 — Capability resolution
        if not self._resolved_capabilities:
            self.resolve_capabilities()

        sources: list[dict[str, str]] = []
        findings: list[dict[str, str]] = []

        # Step 2 — Web search
        web_findings = self._search_web(query, max_sources=max_sources)
        sources.extend(web_findings["sources"])
        findings.extend(web_findings["findings"])

        # Step 3 — PDF extraction (optional)
        if pdf_path:
            pdf_findings = self._extract_pdf(pdf_path)
            sources.extend(pdf_findings["sources"])
            findings.extend(pdf_findings["findings"])

        # Step 4 — Combine all finding texts
        combined_text = self._combine_findings(findings)

        # Step 5 — Summarize
        summary = self._summarize(combined_text)

        # Step 6 — Build report
        report: dict[str, Any] = {
            "query": query,
            "sources": sources,
            "findings": findings,
            "summary": summary,
            "metadata": {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source_count": len(sources),
                "finding_count": len(findings),
                "capabilities_resolved": dict(self._resolved_capabilities),
            },
        }
        return report

    # ------------------------------------------------------------------
    # Pack integration helpers
    # ------------------------------------------------------------------

    def _search_web(self, query: str, max_sources: int = 5) -> dict[str, list]:
        """Use web-search-pack to search the web.

        Returns:
            Dict with ``sources`` and ``findings`` lists.
        """
        sources: list[dict[str, str]] = []
        findings: list[dict[str, str]] = []

        try:
            from web_search_pack import tool as web_tool

            result = web_tool.run(query=query, max_results=max_sources)
            for item in result.get("results", []):
                title = item.get("title", "")
                url = item.get("url", "")
                snippet = item.get("snippet", "")

                sources.append({
                    "title": title,
                    "url": url,
                    "snippet": snippet,
                    "source_type": "web",
                })

                if snippet:
                    findings.append({
                        "source": url or title,
                        "content": snippet,
                    })

            logger.info("Web search returned %d results for '%s'.", len(sources), query)
        except ImportError:
            logger.error(
                "web-search-pack is not installed. "
                "Install it with: pip install web-search-pack"
            )
        except Exception as exc:
            logger.error("Web search failed: %s", exc)

        return {"sources": sources, "findings": findings}

    def _extract_pdf(self, file_path: str) -> dict[str, list]:
        """Use pdf-reader-pack to extract content from a PDF.

        Returns:
            Dict with ``sources`` and ``findings`` lists.
        """
        sources: list[dict[str, str]] = []
        findings: list[dict[str, str]] = []

        try:
            from pdf_reader_pack import tool as pdf_tool

            result = pdf_tool.run(file_path=file_path, pages="all")
            full_text = result.get("text", "").strip()

            sources.append({
                "title": os.path.basename(file_path),
                "url": file_path,
                "snippet": full_text[:200] + ("..." if len(full_text) > 200 else ""),
                "source_type": "pdf",
            })

            # Break PDF content into per-page findings for granularity
            for page in result.get("pages", []):
                page_content = page.get("content", "").strip()
                if page_content:
                    findings.append({
                        "source": f"{os.path.basename(file_path)} (page {page.get('page_number', '?')})",
                        "content": page_content,
                    })

            # Include table data as separate findings
            for table in result.get("tables", []):
                table_rows = table.get("data", [])
                if table_rows:
                    table_text = "\n".join(
                        " | ".join(str(cell) for cell in row if cell)
                        for row in table_rows
                        if row
                    )
                    if table_text.strip():
                        findings.append({
                            "source": f"{os.path.basename(file_path)} (table, page {table.get('page_number', '?')})",
                            "content": table_text,
                        })

            logger.info(
                "PDF extraction yielded %d findings from '%s'.",
                len(findings),
                file_path,
            )
        except ImportError:
            logger.error(
                "pdf-reader-pack is not installed. "
                "Install it with: pip install pdf-reader-pack"
            )
        except FileNotFoundError:
            logger.error("PDF file not found: %s", file_path)
        except Exception as exc:
            logger.error("PDF extraction failed: %s", exc)

        return {"sources": sources, "findings": findings}

    def _combine_findings(self, findings: list[dict[str, str]]) -> str:
        """Merge all finding contents into a single text block for
        summarization."""
        parts: list[str] = []
        for f in findings:
            source = f.get("source", "unknown")
            content = f.get("content", "")
            if content:
                parts.append(f"[{source}]\n{content}")
        return "\n\n".join(parts)

    def _summarize(self, text: str, max_sentences: int = 7) -> str:
        """Use document-summarizer-pack to summarize combined findings.

        Falls back to a simple truncation if the pack is unavailable.

        Args:
            text: The combined text to summarize.
            max_sentences: Maximum sentences in the summary.

        Returns:
            A summary string.
        """
        if not text.strip():
            return "No findings to summarize."

        try:
            from document_summarizer_pack import tool as summarizer_tool

            result = summarizer_tool.run(
                text=text,
                max_sentences=max_sentences,
                method="extractive",
            )
            summary = result.get("summary", "")
            if summary:
                logger.info(
                    "Summarization complete (compression ratio: %s).",
                    result.get("compression_ratio"),
                )
                return summary
        except ImportError:
            logger.error(
                "document-summarizer-pack is not installed. "
                "Install it with: pip install document-summarizer-pack"
            )
        except Exception as exc:
            logger.error("Summarization failed: %s", exc)

        # Fallback: return the first 500 characters as a crude summary
        logger.info("Using fallback truncation for summary.")
        truncated = text[:500].rsplit(" ", 1)[0]
        return truncated + ("..." if len(text) > 500 else "")

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _probe_local(module_name: str) -> bool:
        """Check whether a Python module is importable."""
        import importlib

        try:
            importlib.import_module(module_name)
            return True
        except ImportError:
            return False
