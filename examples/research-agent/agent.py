"""Reference Research Agent — demonstrates the AgentNode core flow.

Uses the 3 MVP starter packs:
  1. web-search-pack       (web_search)
  2. webpage-extractor-pack (webpage_extraction)
  3. pdf-reader-pack        (pdf_extraction)

Flow: Capability Resolution → Installation → Integration.

Prerequisites:
    pip install agentnode-sdk
    agentnode install web-search-pack
    agentnode install webpage-extractor-pack
    agentnode install pdf-reader-pack
"""

from __future__ import annotations

import importlib
import json
import sys


def load_pack(module_name: str):
    """Import an installed ANP pack and return its run() function."""
    try:
        mod = importlib.import_module(module_name)
        return mod.run
    except ImportError:
        print(f"ERROR: {module_name} not installed. Run: agentnode install <pack>")
        sys.exit(1)


def research(query: str, max_results: int = 3, fetch_top: int = 2) -> dict:
    """Run a research pipeline: search → extract top pages → compile summary."""

    # Load tools from installed packs
    web_search = load_pack("web_search_pack.tool")
    webpage_extract = load_pack("webpage_extractor_pack.tool")

    print(f"\n--- Research: {query!r} ---\n")

    # Step 1: Web search
    print("[1/3] Searching the web...")
    search_results = web_search(query=query, max_results=max_results)
    hits = search_results.get("results", [])
    print(f"      Found {len(hits)} results")

    for i, hit in enumerate(hits):
        print(f"      {i+1}. {hit['title']}")
        print(f"         {hit['url']}")

    # Step 2: Extract content from top pages
    print(f"\n[2/3] Extracting content from top {fetch_top} pages...")
    pages = []
    for hit in hits[:fetch_top]:
        url = hit.get("url", "")
        if not url:
            continue
        print(f"      Fetching: {url}")
        try:
            page = webpage_extract(url=url)
            if page.get("text"):
                pages.append({
                    "title": page.get("title", hit.get("title", "")),
                    "url": url,
                    "text": page["text"][:2000],  # truncate for demo
                })
                print(f"      -> {len(page['text'])} chars extracted")
            else:
                print(f"      -> No content extracted")
        except Exception as e:
            print(f"      -> Error: {e}")

    # Step 3: Compile research report
    print("\n[3/3] Compiling report...")
    report = {
        "query": query,
        "sources_found": len(hits),
        "sources_extracted": len(pages),
        "sources": [
            {"title": p["title"], "url": p["url"], "excerpt": p["text"][:500]}
            for p in pages
        ],
        "search_results": [
            {"title": h["title"], "url": h["url"], "snippet": h.get("snippet", "")}
            for h in hits
        ],
    }

    return report


def research_pdf(file_path: str, pages: str = "all") -> dict:
    """Extract and summarize a PDF document."""
    pdf_extract = load_pack("pdf_reader_pack.tool")

    print(f"\n--- PDF Research: {file_path} ---\n")
    print("[1/1] Extracting PDF content...")

    result = pdf_extract(file_path=file_path, pages=pages)

    page_count = len(result.get("pages", []))
    table_count = len(result.get("tables", []))
    text_len = len(result.get("text", ""))

    print(f"      Extracted {page_count} pages, {table_count} tables, {text_len} chars")

    return {
        "file": file_path,
        "pages_extracted": page_count,
        "tables_found": table_count,
        "text_length": text_len,
        "text_preview": result.get("text", "")[:1000],
        "tables": result.get("tables", [])[:5],
    }


def main():
    """CLI entry point for the research agent."""
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python agent.py search <query>       — Web research pipeline")
        print("  python agent.py pdf <file_path>       — PDF extraction")
        print()
        print("Examples:")
        print("  python agent.py search 'AI agent frameworks 2026'")
        print("  python agent.py pdf report.pdf")
        sys.exit(0)

    command = sys.argv[1]

    if command == "search":
        query = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else "AI agent capabilities"
        report = research(query)
        print("\n--- Report ---")
        print(json.dumps(report, indent=2, ensure_ascii=False)[:3000])
    elif command == "pdf":
        if len(sys.argv) < 3:
            print("Usage: python agent.py pdf <file_path>")
            sys.exit(1)
        file_path = sys.argv[2]
        pages = sys.argv[3] if len(sys.argv) > 3 else "all"
        report = research_pdf(file_path, pages)
        print("\n--- Report ---")
        print(json.dumps(report, indent=2, ensure_ascii=False)[:3000])
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
