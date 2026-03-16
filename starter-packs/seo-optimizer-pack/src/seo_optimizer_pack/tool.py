"""SEO analysis tool that checks HTML for common optimization issues."""

from __future__ import annotations

import re
import urllib.request
from collections import Counter
from html.parser import HTMLParser


def _fetch_html(url: str) -> str:
    """Fetch HTML from a URL using urllib (stdlib)."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; SEO-Analyzer/1.0)"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode(errors="replace")


def _get_text_content(soup) -> str:
    """Extract visible text from a BeautifulSoup document."""
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(separator=" ", strip=True)


def _keyword_density(text: str, keyword: str) -> float:
    """Calculate keyword density as a percentage."""
    if not keyword or not text:
        return 0.0
    words = text.lower().split()
    if not words:
        return 0.0
    kw_lower = keyword.lower()
    count = sum(1 for w in words if kw_lower in w)
    return round((count / len(words)) * 100, 2)


def run(
    html: str | None = None,
    url: str | None = None,
    keyword: str = "",
) -> dict:
    """Analyze HTML content for SEO issues.

    Provide either raw HTML or a URL to fetch. Optionally supply a target
    keyword for density analysis.

    Args:
        html: Raw HTML string to analyze.
        url: URL to fetch and analyze (used if html is None).
        keyword: Target keyword/phrase for density analysis.

    Returns:
        dict with keys: score, issues, recommendations, meta.
    """
    from bs4 import BeautifulSoup

    if html is None and url is None:
        return {"score": 0, "issues": ["No HTML or URL provided"], "recommendations": [], "meta": {}}

    if html is None:
        try:
            html = _fetch_html(url)  # type: ignore[arg-type]
        except Exception as exc:
            return {"score": 0, "issues": [f"Failed to fetch URL: {exc}"], "recommendations": [], "meta": {}}

    soup = BeautifulSoup(html, "html.parser")

    issues: list[str] = []
    recommendations: list[str] = []
    score = 100  # Start at 100 and deduct

    # ------------------------------------------------------------------
    # 1. Title tag
    # ------------------------------------------------------------------
    title_tag = soup.find("title")
    title_text = title_tag.get_text(strip=True) if title_tag else ""
    if not title_text:
        issues.append("Missing <title> tag")
        recommendations.append("Add a descriptive <title> tag between 30-60 characters")
        score -= 15
    elif len(title_text) < 30:
        issues.append(f"Title tag too short ({len(title_text)} chars)")
        recommendations.append("Expand title to at least 30 characters for better CTR")
        score -= 5
    elif len(title_text) > 60:
        issues.append(f"Title tag too long ({len(title_text)} chars)")
        recommendations.append("Shorten title to 60 characters or fewer to avoid truncation in SERPs")
        score -= 5

    if keyword and title_text and keyword.lower() not in title_text.lower():
        issues.append("Target keyword not found in title tag")
        recommendations.append(f"Include '{keyword}' in the <title> tag for relevance")
        score -= 5

    # ------------------------------------------------------------------
    # 2. Meta description
    # ------------------------------------------------------------------
    meta_desc_tag = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    meta_desc = meta_desc_tag.get("content", "") if meta_desc_tag else ""
    if not meta_desc:
        issues.append("Missing meta description")
        recommendations.append("Add a meta description between 120-160 characters")
        score -= 10
    elif len(meta_desc) < 120:
        issues.append(f"Meta description too short ({len(meta_desc)} chars)")
        recommendations.append("Expand meta description to at least 120 characters")
        score -= 5
    elif len(meta_desc) > 160:
        issues.append(f"Meta description too long ({len(meta_desc)} chars)")
        recommendations.append("Shorten meta description to 160 characters or fewer")
        score -= 3

    if keyword and meta_desc and keyword.lower() not in meta_desc.lower():
        issues.append("Target keyword not found in meta description")
        recommendations.append(f"Include '{keyword}' in the meta description")
        score -= 3

    # ------------------------------------------------------------------
    # 3. Heading structure
    # ------------------------------------------------------------------
    headings: dict[str, list[str]] = {}
    for level in range(1, 7):
        tag = f"h{level}"
        found = soup.find_all(tag)
        if found:
            headings[tag] = [h.get_text(strip=True) for h in found]

    if "h1" not in headings:
        issues.append("Missing H1 heading")
        recommendations.append("Add exactly one H1 heading that includes your target keyword")
        score -= 10
    elif len(headings.get("h1", [])) > 1:
        issues.append(f"Multiple H1 headings found ({len(headings['h1'])})")
        recommendations.append("Use only one H1 heading per page")
        score -= 5

    if keyword and headings.get("h1"):
        if not any(keyword.lower() in h.lower() for h in headings["h1"]):
            issues.append("Target keyword not found in H1")
            recommendations.append(f"Include '{keyword}' in the H1 heading")
            score -= 5

    # ------------------------------------------------------------------
    # 4. Image alt attributes
    # ------------------------------------------------------------------
    images = soup.find_all("img")
    images_without_alt = [img for img in images if not img.get("alt", "").strip()]
    if images and images_without_alt:
        pct = round(len(images_without_alt) / len(images) * 100)
        issues.append(f"{len(images_without_alt)} of {len(images)} images missing alt text ({pct}%)")
        recommendations.append("Add descriptive alt text to all images for accessibility and SEO")
        score -= min(10, len(images_without_alt) * 2)

    # ------------------------------------------------------------------
    # 5. Links analysis
    # ------------------------------------------------------------------
    links = soup.find_all("a", href=True)
    internal_links = []
    external_links = []
    for link in links:
        href = link["href"]
        if href.startswith(("http://", "https://")):
            external_links.append(href)
        elif href.startswith("/") or href.startswith("#"):
            internal_links.append(href)
        else:
            internal_links.append(href)

    if len(internal_links) < 3:
        issues.append(f"Very few internal links ({len(internal_links)})")
        recommendations.append("Add more internal links to improve site structure and crawlability")
        score -= 5

    # ------------------------------------------------------------------
    # 6. Keyword density
    # ------------------------------------------------------------------
    visible_text = _get_text_content(soup)
    density = _keyword_density(visible_text, keyword) if keyword else 0.0
    word_count = len(visible_text.split())

    if keyword:
        if density == 0:
            issues.append("Target keyword not found in page content")
            recommendations.append(f"Include '{keyword}' naturally throughout the content")
            score -= 10
        elif density < 0.5:
            issues.append(f"Keyword density very low ({density}%)")
            recommendations.append(f"Increase usage of '{keyword}' (aim for 1-2% density)")
            score -= 3
        elif density > 3.0:
            issues.append(f"Keyword density too high ({density}%) -- risk of keyword stuffing")
            recommendations.append("Reduce keyword usage to avoid over-optimization penalties")
            score -= 5

    if word_count < 300:
        issues.append(f"Thin content ({word_count} words)")
        recommendations.append("Expand page content to at least 300 words for better rankings")
        score -= 10

    # ------------------------------------------------------------------
    # 7. Viewport meta
    # ------------------------------------------------------------------
    viewport = soup.find("meta", attrs={"name": "viewport"})
    if not viewport:
        issues.append("Missing viewport meta tag")
        recommendations.append("Add <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"> for mobile-friendliness")
        score -= 5

    # ------------------------------------------------------------------
    # 8. Canonical tag
    # ------------------------------------------------------------------
    canonical = soup.find("link", attrs={"rel": "canonical"})
    if not canonical:
        issues.append("Missing canonical link tag")
        recommendations.append("Add a <link rel=\"canonical\"> to prevent duplicate content issues")
        score -= 3

    # Clamp score
    score = max(0, min(100, score))

    meta = {
        "title": title_text,
        "meta_description": meta_desc,
        "headings": headings,
        "word_count": word_count,
        "keyword_density": density,
        "images_total": len(images),
        "images_without_alt": len(images_without_alt),
        "internal_links": len(internal_links),
        "external_links": len(external_links),
    }

    return {
        "score": score,
        "issues": issues,
        "recommendations": recommendations,
        "meta": meta,
    }
