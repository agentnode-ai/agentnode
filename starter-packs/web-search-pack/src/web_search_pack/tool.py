"""Web search tool wrapping duckduckgo-search (MIT)."""

from __future__ import annotations


def run(query: str, max_results: int = 5) -> dict:
    """Search the web using DuckDuckGo.

    Args:
        query: The search query string.
        max_results: Maximum number of results to return (1-20).

    Returns:
        dict with key: results (list of {title, url, snippet}).
    """
    from duckduckgo_search import DDGS

    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=min(max_results, 20)):
            results.append({
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", ""),
            })

    return {"results": results}
