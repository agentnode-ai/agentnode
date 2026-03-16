"""Generate citations in APA, MLA, Chicago, and Harvard styles with BibTeX output."""

from __future__ import annotations

import re


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _last_name(full_name: str) -> str:
    """Extract a last name from 'First Last' or 'Last'."""
    parts = full_name.strip().split()
    return parts[-1] if parts else "Unknown"


def _first_initial(full_name: str) -> str:
    """Extract a first initial like 'J.' from 'John Smith'."""
    parts = full_name.strip().split()
    if len(parts) >= 2:
        return parts[0][0] + "."
    return ""


def _format_authors_apa(authors: list[str]) -> str:
    """Format author list for APA style."""
    if not authors:
        return "Unknown"
    if len(authors) == 1:
        name = authors[0]
        return f"{_last_name(name)}, {_first_initial(name)}"
    if len(authors) == 2:
        a1 = f"{_last_name(authors[0])}, {_first_initial(authors[0])}"
        a2 = f"{_last_name(authors[1])}, {_first_initial(authors[1])}"
        return f"{a1}, & {a2}"
    # 3+ authors
    formatted = []
    for a in authors[:-1]:
        formatted.append(f"{_last_name(a)}, {_first_initial(a)}")
    formatted.append(f"& {_last_name(authors[-1])}, {_first_initial(authors[-1])}")
    return ", ".join(formatted)


def _format_authors_mla(authors: list[str]) -> str:
    """Format author list for MLA style."""
    if not authors:
        return "Unknown"
    if len(authors) == 1:
        name = authors[0].strip().split()
        if len(name) >= 2:
            return f"{name[-1]}, {' '.join(name[:-1])}"
        return name[0]
    if len(authors) == 2:
        first = authors[0].strip().split()
        first_fmt = f"{first[-1]}, {' '.join(first[:-1])}" if len(first) >= 2 else first[0]
        return f"{first_fmt}, and {authors[1]}"
    # 3+ authors
    first = authors[0].strip().split()
    first_fmt = f"{first[-1]}, {' '.join(first[:-1])}" if len(first) >= 2 else first[0]
    return f"{first_fmt}, et al."


def _format_authors_chicago(authors: list[str]) -> str:
    """Format author list for Chicago style (same as MLA for footnotes)."""
    return _format_authors_mla(authors)


def _format_authors_harvard(authors: list[str]) -> str:
    """Format author list for Harvard style (similar to APA)."""
    return _format_authors_apa(authors)


# ---------------------------------------------------------------------------
# Citation generators per source type
# ---------------------------------------------------------------------------

def _cite_book_apa(s: dict) -> str:
    authors = _format_authors_apa(s.get("authors", []))
    year = s.get("year", "n.d.")
    title = s.get("title", "Untitled")
    publisher = s.get("publisher", "")
    edition = s.get("edition", "")
    ed_str = f" ({edition} ed.)." if edition else "."
    pub_str = f" {publisher}." if publisher else ""
    return f"{authors} ({year}). *{title}*{ed_str}{pub_str}"


def _cite_book_mla(s: dict) -> str:
    authors = _format_authors_mla(s.get("authors", []))
    title = s.get("title", "Untitled")
    publisher = s.get("publisher", "")
    year = s.get("year", "")
    pub_str = f" {publisher}," if publisher else ""
    return f"{authors}. *{title}*.{pub_str} {year}."


def _cite_book_chicago(s: dict) -> str:
    authors = _format_authors_chicago(s.get("authors", []))
    title = s.get("title", "Untitled")
    publisher = s.get("publisher", "")
    year = s.get("year", "")
    place = s.get("place", "")
    loc = f"{place}: " if place else ""
    return f"{authors}. *{title}*. {loc}{publisher}, {year}."


def _cite_book_harvard(s: dict) -> str:
    authors = _format_authors_harvard(s.get("authors", []))
    year = s.get("year", "n.d.")
    title = s.get("title", "Untitled")
    edition = s.get("edition", "")
    publisher = s.get("publisher", "")
    place = s.get("place", "")
    ed_str = f" {edition} edn." if edition else ""
    loc = f" {place}:" if place else ""
    return f"{authors} ({year}) *{title}*.{ed_str}{loc} {publisher}."


def _cite_article_apa(s: dict) -> str:
    authors = _format_authors_apa(s.get("authors", []))
    year = s.get("year", "n.d.")
    title = s.get("title", "Untitled")
    journal = s.get("journal", "")
    volume = s.get("volume", "")
    issue = s.get("issue", "")
    pages = s.get("pages", "")
    doi = s.get("doi", "")
    j_str = f" *{journal}*" if journal else ""
    vol_str = f", *{volume}*" if volume else ""
    iss_str = f"({issue})" if issue else ""
    pg_str = f", {pages}" if pages else ""
    doi_str = f" https://doi.org/{doi}" if doi else ""
    return f"{authors} ({year}). {title}.{j_str}{vol_str}{iss_str}{pg_str}.{doi_str}"


def _cite_article_mla(s: dict) -> str:
    authors = _format_authors_mla(s.get("authors", []))
    title = s.get("title", "Untitled")
    journal = s.get("journal", "")
    volume = s.get("volume", "")
    issue = s.get("issue", "")
    year = s.get("year", "")
    pages = s.get("pages", "")
    j_str = f" *{journal}*" if journal else ""
    vol_str = f", vol. {volume}" if volume else ""
    iss_str = f", no. {issue}" if issue else ""
    pg_str = f", pp. {pages}" if pages else ""
    return f'{authors}. "{title}."{j_str}{vol_str}{iss_str}, {year}{pg_str}.'


def _cite_article_chicago(s: dict) -> str:
    authors = _format_authors_chicago(s.get("authors", []))
    title = s.get("title", "Untitled")
    journal = s.get("journal", "")
    volume = s.get("volume", "")
    issue = s.get("issue", "")
    year = s.get("year", "")
    pages = s.get("pages", "")
    j_str = f" *{journal}*" if journal else ""
    vol_str = f" {volume}" if volume else ""
    iss_str = f", no. {issue}" if issue else ""
    pg_str = f": {pages}" if pages else ""
    return f'{authors}. "{title}."{j_str}{vol_str}{iss_str} ({year}){pg_str}.'


def _cite_article_harvard(s: dict) -> str:
    authors = _format_authors_harvard(s.get("authors", []))
    year = s.get("year", "n.d.")
    title = s.get("title", "Untitled")
    journal = s.get("journal", "")
    volume = s.get("volume", "")
    issue = s.get("issue", "")
    pages = s.get("pages", "")
    j_str = f" *{journal}*" if journal else ""
    vol_str = f", {volume}" if volume else ""
    iss_str = f"({issue})" if issue else ""
    pg_str = f", pp. {pages}" if pages else ""
    return f"{authors} ({year}) '{title}',{j_str}{vol_str}{iss_str}{pg_str}."


def _cite_website_apa(s: dict) -> str:
    authors = _format_authors_apa(s.get("authors", []))
    year = s.get("year", "n.d.")
    title = s.get("title", "Untitled")
    site = s.get("site_name", "")
    url = s.get("url", "")
    accessed = s.get("accessed", "")
    site_str = f" *{site}*." if site else ""
    url_str = f" {url}" if url else ""
    return f"{authors} ({year}). {title}.{site_str}{url_str}"


def _cite_website_mla(s: dict) -> str:
    authors = _format_authors_mla(s.get("authors", []))
    title = s.get("title", "Untitled")
    site = s.get("site_name", "")
    year = s.get("year", "")
    url = s.get("url", "")
    accessed = s.get("accessed", "")
    site_str = f" *{site}*" if site else ""
    acc_str = f" Accessed {accessed}." if accessed else ""
    return f'{authors}. "{title}."{site_str}, {year}, {url}.{acc_str}'


def _cite_website_chicago(s: dict) -> str:
    authors = _format_authors_chicago(s.get("authors", []))
    title = s.get("title", "Untitled")
    site = s.get("site_name", "")
    year = s.get("year", "")
    url = s.get("url", "")
    accessed = s.get("accessed", "")
    site_str = f" *{site}*." if site else ""
    acc_str = f" Accessed {accessed}." if accessed else ""
    return f'{authors}. "{title}."{site_str} {year}. {url}.{acc_str}'


def _cite_website_harvard(s: dict) -> str:
    authors = _format_authors_harvard(s.get("authors", []))
    year = s.get("year", "n.d.")
    title = s.get("title", "Untitled")
    url = s.get("url", "")
    accessed = s.get("accessed", "")
    acc_str = f" (Accessed: {accessed})" if accessed else ""
    url_str = f" Available at: {url}" if url else ""
    return f"{authors} ({year}) *{title}*.{url_str}{acc_str}."


# Dispatch table: (source_type, style) -> formatter
_FORMATTERS: dict[tuple[str, str], callable] = {
    ("book", "apa"): _cite_book_apa,
    ("book", "mla"): _cite_book_mla,
    ("book", "chicago"): _cite_book_chicago,
    ("book", "harvard"): _cite_book_harvard,
    ("article", "apa"): _cite_article_apa,
    ("article", "mla"): _cite_article_mla,
    ("article", "chicago"): _cite_article_chicago,
    ("article", "harvard"): _cite_article_harvard,
    ("website", "apa"): _cite_website_apa,
    ("website", "mla"): _cite_website_mla,
    ("website", "chicago"): _cite_website_chicago,
    ("website", "harvard"): _cite_website_harvard,
}


# ---------------------------------------------------------------------------
# BibTeX generator
# ---------------------------------------------------------------------------

def _generate_bibtex(source: dict) -> str:
    """Generate a BibTeX entry for the source."""
    src_type = source.get("type", "book")
    authors = source.get("authors", [])
    title = source.get("title", "Untitled")
    year = source.get("year", "")

    # Build a citation key
    first_author_last = _last_name(authors[0]) if authors else "Unknown"
    key = re.sub(r"[^a-zA-Z0-9]", "", first_author_last).lower() + str(year)

    bib_type_map = {
        "book": "book",
        "article": "article",
        "website": "misc",
    }
    bib_type = bib_type_map.get(src_type, "misc")

    lines = [f"@{bib_type}{{{key},"]
    lines.append(f'  author = {{{" and ".join(authors)}}},')
    lines.append(f"  title = {{{title}}},")
    lines.append(f"  year = {{{year}}},")

    if src_type == "book":
        if source.get("publisher"):
            lines.append(f'  publisher = {{{source["publisher"]}}},')
        if source.get("edition"):
            lines.append(f'  edition = {{{source["edition"]}}},')
        if source.get("place"):
            lines.append(f'  address = {{{source["place"]}}},')
    elif src_type == "article":
        if source.get("journal"):
            lines.append(f'  journal = {{{source["journal"]}}},')
        if source.get("volume"):
            lines.append(f'  volume = {{{source["volume"]}}},')
        if source.get("issue"):
            lines.append(f'  number = {{{source["issue"]}}},')
        if source.get("pages"):
            lines.append(f'  pages = {{{source["pages"]}}},')
        if source.get("doi"):
            lines.append(f'  doi = {{{source["doi"]}}},')
    elif src_type == "website":
        if source.get("url"):
            lines.append(f'  url = {{{source["url"]}}},')
        if source.get("site_name"):
            lines.append(f'  note = {{Accessed from {source["site_name"]}}},')

    lines.append("}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run(
    source: dict,
    style: str = "apa",
) -> dict:
    """Generate a formatted citation and BibTeX entry.

    Args:
        source: Dictionary describing the source. Required keys:
            - type: "book", "article", or "website"
            - authors: List of author names (e.g. ["John Smith", "Jane Doe"])
            - title: Title of the work
            - year: Publication year (int or str)
            Optional keys (vary by type):
            - publisher, edition, place (books)
            - journal, volume, issue, pages, doi (articles)
            - url, site_name, accessed (websites)
        style: Citation style: "apa", "mla", "chicago", or "harvard".

    Returns:
        Dictionary with citation, style, and bibtex.
    """
    style = style.strip().lower()
    valid_styles = ("apa", "mla", "chicago", "harvard")
    if style not in valid_styles:
        raise ValueError(
            f"Unknown style '{style}'. Choose from: {', '.join(valid_styles)}"
        )

    src_type = source.get("type", "book").strip().lower()
    valid_types = ("book", "article", "website")
    if src_type not in valid_types:
        raise ValueError(
            f"Unknown source type '{src_type}'. Choose from: {', '.join(valid_types)}"
        )

    formatter = _FORMATTERS.get((src_type, style))
    if formatter is None:
        raise ValueError(f"No formatter for ({src_type}, {style}).")

    citation = formatter(source)
    bibtex = _generate_bibtex(source)

    return {
        "citation": citation,
        "style": style,
        "bibtex": bibtex,
    }
