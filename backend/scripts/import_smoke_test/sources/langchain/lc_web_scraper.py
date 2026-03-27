"""
Web scraper tool using BeautifulSoup + requests.
Common pattern for agent-based data gathering.
"""

import requests
from bs4 import BeautifulSoup
from langchain.tools import tool


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


@tool
def scrape_webpage(url: str) -> dict:
    """
    Scrape a webpage and return its title, headings, and main text content.

    Args:
        url: The full URL to scrape (must start with http:// or https://)

    Returns:
        dict with title, headings, paragraphs, links, and status_code
    """
    if not url.startswith(("http://", "https://")):
        return {"error": "URL must start with http:// or https://", "url": url}

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # remove scripts and style tags
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()

        title = soup.title.string.strip() if soup.title else ""
        headings = [h.get_text(strip=True) for h in soup.find_all(["h1", "h2", "h3"])]
        paragraphs = [p.get_text(strip=True) for p in soup.find_all("p") if p.get_text(strip=True)]
        links = [
            {"text": a.get_text(strip=True), "href": a.get("href", "")}
            for a in soup.find_all("a", href=True)
        ][:20]  # limit links

        return {
            "url": url,
            "title": title,
            "headings": headings[:10],
            "paragraphs": paragraphs[:20],
            "links": links,
            "status_code": resp.status_code,
            "error": None,
        }
    except requests.RequestException as e:
        return {"error": str(e), "url": url}
    except Exception as e:
        return {"error": f"Parsing error: {e}", "url": url}
