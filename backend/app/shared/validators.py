"""Shared validation helpers for input sanitization.

Central place for validators used across search, admin, packages, etc.
Prevents drift and ensures consistent security boundaries.
"""

import ipaddress
import logging
import re
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# --- Slug validation ---

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}[a-z0-9]$")


def is_valid_slug(value: str) -> bool:
    """Check if value is a valid package/publisher slug."""
    return bool(SLUG_RE.match(value))


# --- URL validation ---

def _ip_is_dangerous(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True if the IP points somewhere we should never let outbound
    traffic reach (private networks, loopback, link-local, multicast, reserved).
    """
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_reserved
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
    )


def resolve_public_ip(hostname: str) -> str | None:
    """Resolve hostname and return the first public IP, or None if no IP
    returned by DNS is public (i.e. every answer is private/loopback/…).

    Used as the second half of the SSRF defence — at delivery/fetch time,
    the caller resolves the host once with this function and either refuses
    the request or hands the resolved IP to the HTTP client. This closes
    the DNS-rebinding gap left by `is_safe_url` (which only sees the
    hostname at validation time).
    """
    if not hostname:
        return None
    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return None
    for family, _type, _proto, _canon, sockaddr in infos:
        addr = sockaddr[0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if _ip_is_dangerous(ip):
            continue
        return str(ip)
    return None


def is_safe_url(value: str, block_private: bool = False) -> bool:
    """Check if URL uses a safe protocol (http/https only).
    If block_private=True, also reject URLs pointing to private/loopback IPs (SSRF prevention).

    P1-S2: when block_private=True and the host is a DNS name, we additionally
    resolve the hostname and verify at least one public IP is returned. A
    malicious DNS server that returns only private IPs (SSRF/DNS-rebinding)
    will cause this to return False. NOTE: this is TOCTOU-racy by construction
    — the DNS answer can change between this call and the actual fetch, so
    callers that do outbound HTTP MUST also re-resolve at delivery time
    (see `resolve_public_ip`).
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
            if _ip_is_dangerous(ip):
                return False
            return True
        except ValueError:
            pass
        # Hostname is a DNS name — resolve and require at least one public IP.
        # This catches the "internal.corp → 10.0.0.1" validation-time class of
        # attack. Delivery-time re-resolution is still required for DNS rebinding.
        if resolve_public_ip(hostname) is None:
            return False
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
