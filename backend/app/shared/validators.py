"""Shared validation helpers for input sanitization.

Central place for validators used across search, admin, packages, etc.
Prevents drift and ensures consistent security boundaries.
"""

import re

# --- Slug validation ---

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}[a-z0-9]$")


def is_valid_slug(value: str) -> bool:
    """Check if value is a valid package/publisher slug."""
    return bool(SLUG_RE.match(value))


# --- URL validation ---

def is_safe_url(value: str) -> bool:
    """Check if URL uses a safe protocol (http/https only)."""
    return value.lower().startswith(("https://", "http://"))


# --- Filter value validation ---

SAFE_FILTER_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def is_safe_filter_value(value: str) -> bool:
    """Check if a value is safe for use in filter expressions (no injection chars)."""
    return bool(SAFE_FILTER_RE.match(value))


# --- Sort key validation ---

def is_allowed_sort(value: str, allowed: set[str]) -> bool:
    """Check if sort value is in the allowed whitelist."""
    return value in allowed


# --- Tag normalization ---

TAG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,48}[a-z0-9]$")


def normalize_tag(value: str) -> str | None:
    """Normalize and validate a tag. Returns None if invalid."""
    tag = value.strip().lower().replace(" ", "-")
    if TAG_RE.match(tag):
        return tag
    return None


# --- Safe identifier for code generation ---

IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def is_safe_identifier(value: str) -> bool:
    """Check if value is a safe Python identifier (no injection risk)."""
    return bool(IDENTIFIER_RE.match(value))


def sanitize_to_identifier(value: str) -> str:
    """Convert a string to a safe Python identifier by replacing invalid chars."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", value)
