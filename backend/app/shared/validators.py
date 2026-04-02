"""Shared validation helpers for input sanitization.

Central place for validators used across search, admin, packages, etc.
Prevents drift and ensures consistent security boundaries.
"""

import ipaddress
import re
from urllib.parse import urlparse

# --- Slug validation ---

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}[a-z0-9]$")


def is_valid_slug(value: str) -> bool:
    """Check if value is a valid package/publisher slug."""
    return bool(SLUG_RE.match(value))


# --- URL validation ---

def is_safe_url(value: str, block_private: bool = False) -> bool:
    """Check if URL uses a safe protocol (http/https only).
    If block_private=True, also reject URLs pointing to private/loopback IPs (SSRF prevention).
    """
    if not value.lower().startswith(("https://", "http://")):
        return False
    if not block_private:
        return True
    try:
        parsed = urlparse(value)
        hostname = parsed.hostname or ""
        if not hostname:
            return False
        # Block obvious private hostnames
        if hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
            return False
        # Try to parse as IP and check if private/reserved
        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local:
                return False
        except ValueError:
            # Not an IP — hostname like "internal.corp" is allowed
            # (DNS resolution check would be better but adds latency)
            pass
        return True
    except Exception:
        return False


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
