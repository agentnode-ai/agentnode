"""News aggregator tool that fetches and parses RSS/Atom feeds."""

from __future__ import annotations

import re
from datetime import datetime


# Default feeds when none are provided
_DEFAULT_FEEDS: list[str] = [
    "https://feeds.bbci.co.uk/news/rss.xml",
    "https://feeds.reuters.com/reuters/topNews",
    "https://techcrunch.com/feed/",
]


def _strip_html(text: str) -> str:
    """Remove HTML tags from a string."""
    return re.sub(r"<[^>]+>", "", text).strip()


def _parse_date(entry) -> str:
    """Extract a publication date string from a feed entry."""
    for attr in ("published", "updated", "created"):
        val = getattr(entry, attr, None)
        if val:
            return val
    # Try parsed struct
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            try:
                return datetime(*parsed[:6]).isoformat()
            except Exception:
                pass
    return ""


def _extract_source(feed) -> str:
    """Extract source name from the feed metadata."""
    if hasattr(feed, "feed"):
        title = getattr(feed.feed, "title", "")
        if title:
            return title
    return ""


def _matches_topic(entry, topic: str) -> bool:
    """Check if an entry matches a given topic (case-insensitive substring)."""
    if not topic:
        return True
    topic_lower = topic.lower()
    title = getattr(entry, "title", "") or ""
    summary = getattr(entry, "summary", "") or ""
    return topic_lower in title.lower() or topic_lower in summary.lower()


def run(
    feeds: list[str] | None = None,
    topic: str = "",
    limit: int = 20,
) -> dict:
    """Aggregate news articles from RSS/Atom feeds.

    Args:
        feeds: List of RSS/Atom feed URLs. Defaults to BBC, Reuters, TechCrunch.
        topic: Optional topic keyword to filter articles by.
        limit: Maximum number of articles to return.

    Returns:
        dict with keys: articles (list of dicts), total (int).
    """
    import feedparser
    import httpx

    if not feeds:
        feeds = list(_DEFAULT_FEEDS)

    articles: list[dict] = []

    client = httpx.Client(
        timeout=15.0,
        headers={"User-Agent": "Mozilla/5.0 (compatible; NewsAggregator/1.0)"},
        follow_redirects=True,
    )

    try:
        for feed_url in feeds:
            try:
                response = client.get(feed_url)
                response.raise_for_status()
                parsed = feedparser.parse(response.text)
            except Exception:
                # If httpx fails, fall back to feedparser's built-in fetching
                try:
                    parsed = feedparser.parse(feed_url)
                except Exception:
                    continue

            source = _extract_source(parsed)

            for entry in parsed.entries:
                if not _matches_topic(entry, topic):
                    continue

                title = getattr(entry, "title", "") or ""
                link = getattr(entry, "link", "") or ""
                summary_raw = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
                summary = _strip_html(summary_raw)
                # Truncate long summaries
                if len(summary) > 500:
                    summary = summary[:497] + "..."

                published = _parse_date(entry)

                articles.append({
                    "title": title,
                    "link": link,
                    "published": published,
                    "summary": summary,
                    "source": source,
                })

                if len(articles) >= limit:
                    break

            if len(articles) >= limit:
                break
    finally:
        client.close()

    # Sort by published date descending (newest first) when possible
    def _sort_key(a):
        d = a.get("published", "")
        return d if d else ""

    articles.sort(key=_sort_key, reverse=True)
    articles = articles[:limit]

    return {
        "articles": articles,
        "total": len(articles),
    }
