"""ArXiv search tool using the public ArXiv API."""

from __future__ import annotations

import urllib.parse
import xml.etree.ElementTree as ET


# ArXiv Atom namespace
_ATOM = "http://www.w3.org/2005/Atom"
_ARXIV = "http://arxiv.org/schemas/atom"
_OPENSEARCH = "http://a9.com/-/spec/opensearch/1.1/"

_SORT_MAP = {
    "relevance": "relevance",
    "lastUpdatedDate": "lastUpdatedDate",
    "submittedDate": "submittedDate",
    "last_updated": "lastUpdatedDate",
    "submitted": "submittedDate",
    "date": "submittedDate",
}


def _parse_entry(entry: ET.Element) -> dict:
    """Parse a single Atom <entry> element into a dict."""
    def _text(tag: str, ns: str = _ATOM) -> str:
        el = entry.find(f"{{{ns}}}{tag}")
        return (el.text or "").strip() if el is not None else ""

    title = " ".join(_text("title").split())  # Collapse whitespace
    abstract = " ".join(_text("summary").split())

    # Authors
    authors: list[str] = []
    for author_el in entry.findall(f"{{{_ATOM}}}author"):
        name_el = author_el.find(f"{{{_ATOM}}}name")
        if name_el is not None and name_el.text:
            authors.append(name_el.text.strip())

    # URL -- prefer the abstract link
    url = ""
    for link_el in entry.findall(f"{{{_ATOM}}}link"):
        rel = link_el.get("rel", "")
        href = link_el.get("href", "")
        if rel == "alternate":
            url = href
            break
        if not url:
            url = href

    published = _text("published")
    updated = _text("updated")

    # Categories
    categories: list[str] = []
    for cat_el in entry.findall(f"{{{_ATOM}}}category"):
        term = cat_el.get("term", "")
        if term:
            categories.append(term)

    return {
        "title": title,
        "authors": authors,
        "abstract": abstract,
        "url": url,
        "published": published or updated,
        "categories": categories,
    }


def run(
    query: str,
    max_results: int = 10,
    sort_by: str = "relevance",
) -> dict:
    """Search ArXiv for academic papers.

    Args:
        query: Search query (supports ArXiv query syntax, e.g. 'all:transformer attention').
        max_results: Maximum number of papers to return (1-100).
        sort_by: Sort order -- 'relevance', 'submittedDate', or 'lastUpdatedDate'.

    Returns:
        dict with keys: papers (list of dicts), total (int).
    """
    import httpx

    max_results = max(1, min(max_results, 100))
    sort_order = _SORT_MAP.get(sort_by.strip(), "relevance")

    params = {
        "search_query": query,
        "start": 0,
        "max_results": max_results,
        "sortBy": sort_order,
        "sortOrder": "descending",
    }

    url = f"http://export.arxiv.org/api/query?{urllib.parse.urlencode(params)}"

    client = httpx.Client(
        timeout=20.0,
        headers={"User-Agent": "Mozilla/5.0 (compatible; ArXivSearch/1.0)"},
        follow_redirects=True,
    )

    try:
        response = client.get(url)
        response.raise_for_status()
        xml_text = response.text
    finally:
        client.close()

    # Parse Atom XML
    root = ET.fromstring(xml_text)

    # Total results from opensearch
    total_el = root.find(f"{{{_OPENSEARCH}}}totalResults")
    total_results = int(total_el.text) if total_el is not None and total_el.text else 0

    papers: list[dict] = []
    for entry in root.findall(f"{{{_ATOM}}}entry"):
        paper = _parse_entry(entry)
        # Skip entries that are just the API boilerplate (no title)
        if paper["title"]:
            papers.append(paper)

    return {
        "papers": papers,
        "total": total_results,
    }
