"""Web search tool using DuckDuckGo HTML endpoint (VCR-interceptable)."""

from __future__ import annotations

import re


def run(query: str, max_results: int = 5, backend: str = "") -> dict:
    """Search the web using DuckDuckGo.

    Args:
        query: The search query string.
        max_results: Maximum number of results to return (1-20).
        backend: "ddgs" to use duckduckgo-search library, empty for httpx.

    Returns:
        dict with key: results (list of {title, url, snippet}).
    """
    max_results = max(1, min(max_results, 20))

    if backend == "ddgs":
        return _search_ddgs(query, max_results)
    return _search_httpx(query, max_results)


def _search_httpx(query: str, max_results: int) -> dict:
    """Search via DuckDuckGo HTML endpoint using httpx (VCR-interceptable)."""
    import httpx

    resp = httpx.post(
        "https://html.duckduckgo.com/html/",
        data={"q": query},
        headers={"User-Agent": "AgentNode/1.0 (search-tool)"},
        follow_redirects=True,
        timeout=15.0,
    )
    resp.raise_for_status()

    results = _parse_ddg_html(resp.text, max_results)
    return {"results": results}


def _parse_ddg_html(html: str, max_results: int) -> list[dict]:
    """Parse DuckDuckGo HTML results page."""
    results = []

    blocks = re.findall(
        r'<a\s+rel="nofollow"\s+class="result__a"\s+href="([^"]*)"[^>]*>(.*?)</a>',
        html,
        re.DOTALL,
    )
    snippets = re.findall(
        r'<a\s+class="result__snippet"[^>]*>(.*?)</a>',
        html,
        re.DOTALL,
    )

    for i, (url, raw_title) in enumerate(blocks[:max_results]):
        title = re.sub(r"<[^>]+>", "", raw_title).strip()
        snippet = ""
        if i < len(snippets):
            snippet = re.sub(r"<[^>]+>", "", snippets[i]).strip()

        if url.startswith("//duckduckgo.com/l/?uddg="):
            from urllib.parse import unquote
            url = unquote(url.split("uddg=")[1].split("&")[0])

        results.append({
            "title": title,
            "url": url,
            "snippet": snippet,
        })

    return results


def _search_ddgs(query: str, max_results: int) -> dict:
    """Fallback: search via duckduckgo-search library (uses primp/Rust HTTP)."""
    from duckduckgo_search import DDGS

    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            results.append({
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", ""),
            })

    return {"results": results}
