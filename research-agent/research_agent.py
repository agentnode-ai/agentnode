#!/usr/bin/env python3
"""
AgentNode Reference Research Agent
===================================

Demonstrates the core AgentNode flow:
  Capability Resolution -> Installation -> Integration

This agent uses the 3 MVP starter packs to perform a research task:
  1. web-search-pack     - Search the web for a topic
  2. webpage-extractor-pack - Extract content from found URLs
  3. pdf-reader-pack     - Extract text from PDF files

Usage:
    python research_agent.py "artificial intelligence safety"
    python research_agent.py "climate change" --pdf report.pdf
    python research_agent.py "quantum computing" --max-results 3 --extract-top 2
"""

from __future__ import annotations

import argparse
import json
import os
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# AgentNode SDK -- used for capability discovery and resolution
# ---------------------------------------------------------------------------
try:
    from agentnode_sdk import AgentNodeClient, AgentNodeError
except ImportError:
    AgentNodeClient = None  # type: ignore[assignment,misc]
    AgentNodeError = Exception  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Starter-pack tool imports (installed as local packages)
# ---------------------------------------------------------------------------
try:
    from web_search_pack import run as web_search
except ImportError:
    web_search = None  # type: ignore[assignment]

try:
    from webpage_extractor_pack import run as extract_webpage
except ImportError:
    extract_webpage = None  # type: ignore[assignment]

try:
    from pdf_reader_pack import run as extract_pdf
except ImportError:
    extract_pdf = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ResearchResult:
    """Holds the collected research for a topic."""
    topic: str
    web_results: list[dict] = field(default_factory=list)
    extracted_pages: list[dict] = field(default_factory=list)
    pdf_content: dict | None = None
    summary: str = ""


# ---------------------------------------------------------------------------
# Capability resolution via AgentNode SDK
# ---------------------------------------------------------------------------

REQUIRED_CAPABILITIES = ["web_search", "webpage_extraction", "pdf_extraction"]

CAPABILITY_TO_PACK = {
    "web_search": "web-search-pack",
    "webpage_extraction": "webpage-extractor-pack",
    "pdf_extraction": "pdf-reader-pack",
}


def resolve_capabilities(api_key: str | None = None) -> dict:
    """
    Use the AgentNode SDK to resolve which packs satisfy our required
    capabilities. This demonstrates the Capability Resolution flow.

    Returns a mapping of capability_id -> resolved package info.
    """
    resolved: dict = {}

    if not AgentNodeClient or not api_key:
        print("[resolve] SDK not available or no API key -- using local packs directly")
        for cap_id, slug in CAPABILITY_TO_PACK.items():
            resolved[cap_id] = {
                "slug": slug,
                "source": "local",
                "version": "1.0.0",
            }
        return resolved

    base_url = os.environ.get("AGENTNODE_BASE_URL", "https://api.agentnode.net/v1")

    try:
        with AgentNodeClient(api_key=api_key, base_url=base_url) as client:
            print(f"[resolve] Resolving capabilities via AgentNode API ({base_url})")
            print(f"[resolve] Required capabilities: {REQUIRED_CAPABILITIES}")

            # Step 1: Resolve -- ask the API which packs match our needs
            result = client.resolve(
                capabilities=REQUIRED_CAPABILITIES,
                runtime="python",
            )

            print(f"[resolve] Found {result.total} matching packages")

            for pkg in result.results:
                for cap_id in pkg.matched_capabilities:
                    if cap_id in REQUIRED_CAPABILITIES and cap_id not in resolved:
                        resolved[cap_id] = {
                            "slug": pkg.slug,
                            "name": pkg.name,
                            "version": pkg.version,
                            "trust_level": pkg.trust_level,
                            "score": pkg.score,
                            "source": "agentnode_api",
                        }
                        print(
                            f"  [{cap_id}] -> {pkg.slug} v{pkg.version} "
                            f"(trust={pkg.trust_level}, score={pkg.score:.2f})"
                        )

            # Step 2: Get install metadata for each resolved pack
            for cap_id, info in resolved.items():
                try:
                    meta = client.get_install_metadata(info["slug"])
                    info["install_mode"] = meta.install_mode
                    info["entrypoint"] = meta.entrypoint
                    info["runtime"] = meta.runtime
                    if meta.permissions:
                        info["network_level"] = meta.permissions.network_level
                    print(
                        f"  [{cap_id}] install_mode={meta.install_mode}, "
                        f"entrypoint={meta.entrypoint}"
                    )
                except AgentNodeError as e:
                    print(f"  [{cap_id}] Could not fetch install metadata: {e}")

    except AgentNodeError as e:
        print(f"[resolve] API error: {e}")
        print("[resolve] Falling back to local packs")
        for cap_id, slug in CAPABILITY_TO_PACK.items():
            resolved[cap_id] = {"slug": slug, "source": "local_fallback", "version": "1.0.0"}

    # Fill in any capabilities not resolved by the API
    for cap_id in REQUIRED_CAPABILITIES:
        if cap_id not in resolved:
            slug = CAPABILITY_TO_PACK[cap_id]
            resolved[cap_id] = {"slug": slug, "source": "local_default", "version": "1.0.0"}
            print(f"  [{cap_id}] -> {slug} (local default)")

    return resolved


# ---------------------------------------------------------------------------
# Research pipeline
# ---------------------------------------------------------------------------

def check_tools_available() -> list[str]:
    """Check which tool packs are importable and return list of missing ones."""
    missing = []
    if web_search is None:
        missing.append("web-search-pack")
    if extract_webpage is None:
        missing.append("webpage-extractor-pack")
    if extract_pdf is None:
        missing.append("pdf-reader-pack")
    return missing


def step_web_search(topic: str, max_results: int = 5) -> list[dict]:
    """Step 1: Search the web for the research topic."""
    if web_search is None:
        print("[search] SKIP: web-search-pack not installed")
        return []

    print(f"\n{'='*60}")
    print(f"STEP 1: Web Search")
    print(f"{'='*60}")
    print(f"  Query: {topic!r}")
    print(f"  Max results: {max_results}")

    result = web_search(query=topic, max_results=max_results)
    results = result.get("results", [])

    print(f"  Found {len(results)} results:\n")
    for i, r in enumerate(results, 1):
        print(f"  {i}. {r['title']}")
        print(f"     {r['url']}")
        print(f"     {r['snippet'][:120]}...")
        print()

    return results


def step_extract_pages(
    web_results: list[dict], extract_top: int = 2
) -> list[dict]:
    """Step 2: Extract full content from the top N search results."""
    if extract_webpage is None:
        print("[extract] SKIP: webpage-extractor-pack not installed")
        return []

    print(f"\n{'='*60}")
    print(f"STEP 2: Webpage Extraction")
    print(f"{'='*60}")

    urls = [r["url"] for r in web_results[:extract_top] if r.get("url")]
    print(f"  Extracting content from top {len(urls)} URLs...\n")

    extracted = []
    for i, url in enumerate(urls, 1):
        print(f"  [{i}/{len(urls)}] {url}")
        try:
            page = extract_webpage(url=url)
            if page.get("error"):
                print(f"         Error: {page['error']}")
            else:
                text = page.get("text", "")
                title = page.get("title", "(no title)")
                print(f"         Title: {title}")
                print(f"         Extracted {len(text)} characters")
                extracted.append(page)
        except Exception as e:
            print(f"         Exception: {e}")

    print(f"\n  Successfully extracted {len(extracted)} pages")
    return extracted


def step_extract_pdf(pdf_path: str, pages: str = "all") -> dict | None:
    """Step 3: Extract text from a PDF file."""
    if extract_pdf is None:
        print("[pdf] SKIP: pdf-reader-pack not installed")
        return None

    print(f"\n{'='*60}")
    print(f"STEP 3: PDF Extraction")
    print(f"{'='*60}")
    print(f"  File: {pdf_path}")
    print(f"  Pages: {pages}")

    if not Path(pdf_path).exists():
        print(f"  ERROR: File not found: {pdf_path}")
        return None

    try:
        result = extract_pdf(file_path=pdf_path, pages=pages)
        page_count = len(result.get("pages", []))
        table_count = len(result.get("tables", []))
        text_len = len(result.get("text", ""))
        print(f"  Extracted {page_count} pages, {table_count} tables, {text_len} characters")
        return result
    except Exception as e:
        print(f"  ERROR: {e}")
        return None


def compile_summary(result: ResearchResult) -> str:
    """Compile a research summary from all collected data."""
    parts = [
        f"Research Summary: {result.topic}",
        "=" * 60,
        "",
    ]

    # Web search summary
    parts.append(f"Web Search: Found {len(result.web_results)} results")
    for i, r in enumerate(result.web_results, 1):
        parts.append(f"  {i}. {r.get('title', '(no title)')}")
    parts.append("")

    # Extracted pages summary
    if result.extracted_pages:
        parts.append(f"Extracted Content: {len(result.extracted_pages)} pages")
        for page in result.extracted_pages:
            title = page.get("title", "(no title)")
            text = page.get("text", "")
            parts.append(f"\n  --- {title} ---")
            # Show first 500 chars as preview
            preview = textwrap.shorten(text, width=500, placeholder="...")
            parts.append(f"  {preview}")
        parts.append("")

    # PDF content summary
    if result.pdf_content:
        text = result.pdf_content.get("text", "")
        page_count = len(result.pdf_content.get("pages", []))
        table_count = len(result.pdf_content.get("tables", []))
        parts.append(f"PDF Content: {page_count} pages, {table_count} tables")
        preview = textwrap.shorten(text, width=500, placeholder="...")
        parts.append(f"  {preview}")
        parts.append("")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def run_research(
    topic: str,
    pdf_path: str | None = None,
    pdf_pages: str = "all",
    max_results: int = 5,
    extract_top: int = 2,
    api_key: str | None = None,
    output_file: str | None = None,
) -> ResearchResult:
    """
    Run the full research pipeline.

    This is the core demonstration of the AgentNode flow:
      1. Resolve capabilities (via SDK)
      2. Use resolved packs to perform research
      3. Compile results
    """
    print(f"\n{'#'*60}")
    print(f"# AgentNode Research Agent")
    print(f"# Topic: {topic}")
    print(f"{'#'*60}")

    # --- Phase 1: Capability Resolution ---
    print(f"\n{'='*60}")
    print("PHASE 1: Capability Resolution")
    print(f"{'='*60}")

    resolved = resolve_capabilities(api_key)
    print(f"\nResolved {len(resolved)} capabilities:")
    for cap_id, info in resolved.items():
        print(f"  {cap_id}: {info['slug']} (source={info['source']})")

    # Check that the tool packs are actually importable
    missing = check_tools_available()
    if missing:
        print(f"\nWARNING: The following packs are not installed locally:")
        for m in missing:
            print(f"  - {m}")
        print("Install them with:")
        for m in missing:
            pack_dir = Path(__file__).parent.parent / "starter-packs" / m
            print(f"  pip install -e {pack_dir}")
        print()

    # --- Phase 2: Research Pipeline ---
    print(f"\n{'='*60}")
    print("PHASE 2: Research Pipeline")
    print(f"{'='*60}")

    result = ResearchResult(topic=topic)

    # Step 1: Web search
    result.web_results = step_web_search(topic, max_results=max_results)

    # Step 2: Extract top web pages
    if result.web_results:
        result.extracted_pages = step_extract_pages(
            result.web_results, extract_top=extract_top
        )

    # Step 3: PDF extraction (optional)
    if pdf_path:
        result.pdf_content = step_extract_pdf(pdf_path, pages=pdf_pages)

    # --- Phase 3: Compile Summary ---
    print(f"\n{'='*60}")
    print("PHASE 3: Research Summary")
    print(f"{'='*60}\n")

    result.summary = compile_summary(result)
    print(result.summary)

    # Write output to file if requested
    if output_file:
        output = {
            "topic": result.topic,
            "web_results": result.web_results,
            "extracted_pages": [
                {
                    "title": p.get("title", ""),
                    "url": p.get("url", ""),
                    "text_length": len(p.get("text", "")),
                    "text_preview": p.get("text", "")[:1000],
                }
                for p in result.extracted_pages
            ],
            "pdf_content": {
                "text_length": len(result.pdf_content.get("text", "")),
                "page_count": len(result.pdf_content.get("pages", [])),
                "table_count": len(result.pdf_content.get("tables", [])),
            } if result.pdf_content else None,
        }
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        print(f"\nResults written to: {output_file}")

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="AgentNode Reference Research Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python research_agent.py "artificial intelligence safety"
              python research_agent.py "climate change" --pdf report.pdf
              python research_agent.py "quantum computing" --max-results 3 --extract-top 2
              python research_agent.py "machine learning" --output results.json
        """),
    )
    parser.add_argument(
        "topic",
        help="The research topic to investigate",
    )
    parser.add_argument(
        "--pdf",
        metavar="FILE",
        help="Path to a PDF file to include in the research",
    )
    parser.add_argument(
        "--pdf-pages",
        default="all",
        help="PDF page range to extract (default: all, e.g. '1-5')",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=5,
        help="Maximum number of web search results (default: 5)",
    )
    parser.add_argument(
        "--extract-top",
        type=int,
        default=2,
        help="Number of top search results to extract full content from (default: 2)",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("AGENTNODE_API_KEY"),
        help="AgentNode API key (or set AGENTNODE_API_KEY env var)",
    )
    parser.add_argument(
        "--output", "-o",
        metavar="FILE",
        help="Write results to a JSON file",
    )

    args = parser.parse_args()

    run_research(
        topic=args.topic,
        pdf_path=args.pdf,
        pdf_pages=args.pdf_pages,
        max_results=args.max_results,
        extract_top=args.extract_top,
        api_key=args.api_key,
        output_file=args.output,
    )


if __name__ == "__main__":
    main()
