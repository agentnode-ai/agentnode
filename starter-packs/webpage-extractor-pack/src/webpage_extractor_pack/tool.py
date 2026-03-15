"""Webpage extraction tool wrapping trafilatura (Apache-2.0)."""

from __future__ import annotations


def run(url: str, include_links: bool = False) -> dict:
    """Extract clean text and metadata from a webpage.

    Args:
        url: The URL to fetch and extract from.
        include_links: Whether to include hyperlinks in the extracted text.

    Returns:
        dict with keys: title, text, url, error (if any).
    """
    import json

    import trafilatura

    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        return {"title": "", "text": "", "url": url, "error": "Failed to fetch URL"}

    text = trafilatura.extract(downloaded, include_links=include_links) or ""
    metadata_str = trafilatura.extract(downloaded, output_format="json")

    meta: dict = {}
    if metadata_str:
        try:
            meta = json.loads(metadata_str)
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        "title": meta.get("title", ""),
        "text": text,
        "url": url,
    }
