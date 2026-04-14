"""Resource content provider — reads content for resource:// URIs.

Supports:
- resource://slug/name → reads from installed package resources/ directory
- https:// → uri_reference only (no implicit fetching)

Content is read from the installed package directory:
  resources/{name}.json, resources/{name}.txt, etc.

Max content size: 100KB (hard limit).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("agentnode.resource_provider")

MAX_CONTENT_BYTES = 100 * 1024  # 100KB


@dataclass
class ResourceContent:
    """Result of reading a resource."""

    type: str  # "inline" | "uri_reference" | "metadata_only"
    content: str | None = None
    mime_type: str | None = None
    uri: str | None = None
    name: str | None = None
    description: str | None = None


def read_content(uri: str, *, installed_packages_dir: str | None = None) -> ResourceContent:
    """Read content for a resource URI.

    - resource://slug/name → looks for local content files
    - https:// → returns uri_reference (no fetch)
    - Anything else → raises ValueError
    """
    if uri.startswith("https://"):
        return ResourceContent(
            type="uri_reference",
            uri=uri,
        )

    if uri.startswith("resource://"):
        return _read_resource_uri(uri, installed_packages_dir)

    raise ValueError(f"Unsupported URI scheme: {uri}")


def _read_resource_uri(uri: str, base_dir: str | None = None) -> ResourceContent:
    """Read content from a resource:// URI.

    URI format: resource://slug/name
    Looks for files in: {base_dir}/{slug_underscored}/resources/{name}.*
    """
    # Parse resource://slug/name
    path_part = uri[len("resource://"):]
    parts = path_part.split("/", 1)
    if len(parts) != 2:
        return ResourceContent(type="metadata_only", uri=uri)

    slug, name = parts

    # Determine base directory
    if base_dir:
        pkg_dir = Path(base_dir)
    else:
        try:
            from agentnode_sdk.config import config_dir
            pkg_dir = config_dir() / "packages"
        except Exception:
            return ResourceContent(type="metadata_only", uri=uri)

    # Look for content files
    slug_dir = slug.replace("-", "_")
    resources_dir = pkg_dir / slug_dir / "resources"

    if not resources_dir.exists():
        # Try without underscore conversion
        resources_dir = pkg_dir / slug / "resources"
        if not resources_dir.exists():
            return ResourceContent(type="metadata_only", uri=uri)

    # Try common extensions
    for ext in (".json", ".txt", ".md", ".yaml", ".yml", ".csv", ".xml"):
        content_path = resources_dir / f"{name}{ext}"
        if content_path.exists():
            return _read_file_content(content_path, uri, name)

    # Try exact name (no extension)
    content_path = resources_dir / name
    if content_path.exists():
        return _read_file_content(content_path, uri, name)

    return ResourceContent(type="metadata_only", uri=uri)


def _read_file_content(path: Path, uri: str, name: str) -> ResourceContent:
    """Read a content file with size limit."""
    size = path.stat().st_size
    if size > MAX_CONTENT_BYTES:
        return ResourceContent(
            type="metadata_only",
            uri=uri,
            name=name,
            description=f"Content too large ({size} bytes, max {MAX_CONTENT_BYTES})",
        )

    mime_type = _guess_mime(path.suffix)

    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ResourceContent(
            type="metadata_only",
            uri=uri,
            name=name,
            description="Binary content not supported",
        )

    return ResourceContent(
        type="inline",
        content=content,
        mime_type=mime_type,
        uri=uri,
        name=name,
    )


def _guess_mime(suffix: str) -> str:
    """Guess MIME type from file extension."""
    return {
        ".json": "application/json",
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".yaml": "application/yaml",
        ".yml": "application/yaml",
        ".csv": "text/csv",
        ".xml": "application/xml",
    }.get(suffix.lower(), "text/plain")
