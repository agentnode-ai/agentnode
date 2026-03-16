"""Markdown notes vault manager with YAML frontmatter (stdlib only)."""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path


def run(operation: str, vault_path: str, **kwargs) -> dict:
    """Manage Markdown notes in a local vault directory.

    Args:
        operation: One of "create", "search", "list", "get", "list_tags".
        vault_path: Path to the notes vault directory.
        **kwargs:
            title (str): Note title (for "create", "get").
            content (str): Note body (for "create").
            tags (list[str]): Tags (for "create").
            query (str): Search query (for "search").
            tag (str): Tag filter (for "list").

    Returns:
        dict varying by operation.
    """
    vault = Path(vault_path)
    vault.mkdir(parents=True, exist_ok=True)

    ops = {
        "create": _create,
        "search": _search,
        "list": _list_notes,
        "get": _get,
        "list_tags": _list_tags,
    }

    if operation not in ops:
        raise ValueError(f"Unknown operation: {operation}. Choose from {list(ops)}")

    return ops[operation](vault, **kwargs)


def _slugify(title: str) -> str:
    """Convert a title into a filesystem-safe filename slug."""
    slug = re.sub(r"[^\w\s-]", "", title.lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return slug or "untitled"


def _build_frontmatter(title: str, tags: list[str], created: str, modified: str) -> str:
    """Build YAML frontmatter block."""
    tag_str = ", ".join(f'"{t}"' for t in tags)
    return (
        "---\n"
        f"title: \"{title}\"\n"
        f"tags: [{tag_str}]\n"
        f"created: \"{created}\"\n"
        f"modified: \"{modified}\"\n"
        "---\n"
    )


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from a Markdown string. Returns (metadata, body)."""
    meta: dict = {}
    body = text

    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
    if match:
        fm_block = match.group(1)
        body = match.group(2)

        # Simple key-value parser for our known frontmatter fields
        for line in fm_block.splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                key = key.strip()
                value = value.strip()

                if key == "tags":
                    # Parse [tag1, tag2] or ["tag1", "tag2"]
                    inner = value.strip("[]")
                    tags = [t.strip().strip('"').strip("'") for t in inner.split(",") if t.strip()]
                    meta["tags"] = tags
                else:
                    meta[key] = value.strip('"').strip("'")

    return meta, body


def _create(vault: Path, **kwargs) -> dict:
    title = kwargs.get("title", "Untitled")
    content = kwargs.get("content", "")
    tags = kwargs.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]

    now = datetime.now().isoformat()
    slug = _slugify(title)
    filename = f"{slug}.md"
    filepath = vault / filename

    # Avoid overwriting: append a numeric suffix if needed
    counter = 1
    while filepath.exists():
        filename = f"{slug}-{counter}.md"
        filepath = vault / filename
        counter += 1

    fm = _build_frontmatter(title, tags, now, now)
    full = f"{fm}\n# {title}\n\n{content}\n"

    filepath.write_text(full, encoding="utf-8")

    return {
        "status": "created",
        "title": title,
        "path": str(filepath),
        "tags": tags,
        "created": now,
    }


def _search(vault: Path, **kwargs) -> dict:
    query = kwargs.get("query", "")
    if not query:
        raise ValueError("query is required for search")

    query_lower = query.lower()
    results = []

    for md_file in vault.glob("*.md"):
        text = md_file.read_text(encoding="utf-8")
        if query_lower in text.lower():
            meta, body = _parse_frontmatter(text)
            # Find matching lines for snippet
            lines = text.splitlines()
            snippets = [line.strip() for line in lines if query_lower in line.lower()]
            results.append({
                "title": meta.get("title", md_file.stem),
                "path": str(md_file),
                "tags": meta.get("tags", []),
                "snippets": snippets[:3],
            })

    return {"results": results, "total": len(results), "query": query}


def _list_notes(vault: Path, **kwargs) -> dict:
    tag_filter = kwargs.get("tag", "")
    notes = []

    for md_file in sorted(vault.glob("*.md")):
        text = md_file.read_text(encoding="utf-8")
        meta, _ = _parse_frontmatter(text)
        tags = meta.get("tags", [])

        if tag_filter and tag_filter not in tags:
            continue

        notes.append({
            "title": meta.get("title", md_file.stem),
            "path": str(md_file),
            "tags": tags,
            "created": meta.get("created", ""),
            "modified": meta.get("modified", ""),
        })

    return {"notes": notes, "total": len(notes)}


def _get(vault: Path, **kwargs) -> dict:
    title = kwargs.get("title", "")
    if not title:
        raise ValueError("title is required for get")

    # Try exact slug match first, then search by frontmatter title
    slug = _slugify(title)
    filepath = vault / f"{slug}.md"

    if not filepath.exists():
        # Scan all files for matching title in frontmatter
        for md_file in vault.glob("*.md"):
            text = md_file.read_text(encoding="utf-8")
            meta, _ = _parse_frontmatter(text)
            if meta.get("title", "").lower() == title.lower():
                filepath = md_file
                break
        else:
            return {"error": f"Note not found: {title}"}

    text = filepath.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(text)

    return {
        "title": meta.get("title", filepath.stem),
        "path": str(filepath),
        "tags": meta.get("tags", []),
        "created": meta.get("created", ""),
        "modified": meta.get("modified", ""),
        "content": body.strip(),
    }


def _list_tags(vault: Path, **kwargs) -> dict:
    tag_counts: dict[str, int] = {}

    for md_file in vault.glob("*.md"):
        text = md_file.read_text(encoding="utf-8")
        meta, _ = _parse_frontmatter(text)
        for tag in meta.get("tags", []):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    tags = [{"tag": t, "count": c} for t, c in sorted(tag_counts.items())]
    return {"tags": tags, "total": len(tags)}
