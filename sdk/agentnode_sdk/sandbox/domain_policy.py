"""Pure, lifecycle-free domain allowlist policy (Stage 5).

ONE canonicalizer for ``allowed_domains``, shared by Stage-2 egress validation and
Stage-5 install-seal. NO docker / proxy / DNS / runtime / network deps — pure syntax +
policy + canonicalization. The backend keeps its own server-authoritative mirror; a shared
test-vector file (sdk/tests/data/allowed_domains_vectors.json) proves both decide
identically (any divergence is a test failure).

Policy (fail-closed): bare ASCII LDH hostnames only. Lowercased; a single trailing dot
stripped; de-duplicated; sorted. Rejects scheme/port/path/query/fragment/userinfo/
wildcard/backslash/whitespace/control chars; IP literals (incl. loopback/private/
link-local/multicast/metadata); ``localhost``; single-label names; empty labels; labels
with ``_``; leading/trailing-hyphen or over-long (>63) labels; over-long (>253) domains;
raw Unicode/IDN (punycode ``xn--`` is ASCII and is allowed only if it satisfies the LDH
label rules). No DNS resolution.
"""
from __future__ import annotations

import ipaddress
import re

# LDH label: a-z 0-9 hyphen; 1..63 chars; no leading/trailing hyphen; no underscore.
_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_MAX_DOMAIN_LEN = 253
_FORBIDDEN_SUBSTR = ("://", "/", "?", "#", "@", "*", "\\", ":")


class DomainPolicyError(ValueError):
    """An allowed_domains entry (or the list) violates the policy. Subclasses ValueError
    so existing ``ValueError`` call sites (Stage-2 egress) keep working."""


def _canonicalize_one(d) -> str:
    if not isinstance(d, str):
        raise DomainPolicyError(f"domain must be a string: {d!r}")
    h = d.strip()
    if not h:
        raise DomainPolicyError("empty domain")
    # control chars / whitespace
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in h) or " " in h or "\t" in h:
        raise DomainPolicyError(f"control/whitespace char in domain: {d!r}")
    # scheme / port / path / query / fragment / userinfo / wildcard / backslash
    for bad in _FORBIDDEN_SUBSTR:
        if bad in h:
            raise DomainPolicyError(
                f"domain must be a bare hostname (no scheme/port/path/wildcard): {d!r}"
            )
    # ASCII only — reject raw Unicode/IDN (punycode xn-- is ASCII and passes LDH below).
    try:
        h.encode("ascii")
    except UnicodeEncodeError:
        raise DomainPolicyError(f"non-ASCII domain not allowed (use punycode): {d!r}")
    h = h.lower()
    if h.endswith("."):          # strip exactly ONE trailing dot
        h = h[:-1]
    if not h:
        raise DomainPolicyError("empty domain")
    # IP literals (any: loopback/private/link-local/metadata/public) are not hostnames.
    try:
        ipaddress.ip_address(h)
    except ValueError:
        pass
    else:
        raise DomainPolicyError(f"IP literals are not allowed (use a hostname): {d!r}")
    if h == "localhost":
        raise DomainPolicyError("localhost is not allowed")
    if len(h) > _MAX_DOMAIN_LEN:
        raise DomainPolicyError(f"domain too long: {d!r}")
    labels = h.split(".")
    if len(labels) < 2:
        raise DomainPolicyError(f"need a fully-qualified domain (>=2 labels): {d!r}")
    for lab in labels:
        if not _LABEL.match(lab):
            raise DomainPolicyError(f"invalid domain label {lab!r} in {d!r}")
    return h


def canonicalize_allowed_domains(domains) -> tuple[str, ...]:
    """Return a sorted, de-duplicated tuple of canonical bare hostnames, or raise
    DomainPolicyError. Non-empty list/tuple required."""
    if not isinstance(domains, (list, tuple)) or len(domains) == 0:
        raise DomainPolicyError("allowed_domains must be a non-empty list")
    return tuple(sorted({_canonicalize_one(d) for d in domains}))
