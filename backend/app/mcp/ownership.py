"""Slice 2b-1 — ownership evidence model (framework only, activates nothing).

Defines what counts as STRONG ownership for MCP auto-publish and derives a
confidence from a PublisherPackageClaim. NO mechanism produces strong evidence
yet (publish-challenge is Slice 2b-2; npm provenance + repo-control is 2b-3), so
today every real claim is at most ADMIN_ATTESTED (a human admin attestation — the
review path, NOT an automated proof) or NONE. The auto gate therefore stays
`passed:false` for all real submissions. This slice only wires the taxonomy so a
later mechanism can flip a claim to STRONG.

No I/O here. No migration: method/strength/status are VARCHAR and evidence is
JSONB on the existing PublisherPackageClaim.

Founder line: auto-publish ONLY on STRONG evidence; a forged repo link (Medium)
never suffices; everything else is review-fallback.
"""

from __future__ import annotations

import hashlib
import secrets

# Confidence levels (derived, stored in the gate_result JSONB).
STRONG = "strong"
MEDIUM = "medium"
WEAK = "weak"
ADMIN_ATTESTED = "admin_attested"
NONE = "none"

# --- publish-challenge (Slice 2b-2) ------------------------------------------
# The publisher proves package control by publishing a version whose keywords
# carry a server-issued token: only someone with publish rights can do that.
# The token is shown ONCE and stored only as a SHA-256 hash.
CHALLENGE_KEYWORD_PREFIX = "agentnode-ownership-"
CHALLENGE_PENDING_TTL_DAYS = 30  # unverified challenge lifetime
CHALLENGE_STATUS_PENDING = "pending"


def new_challenge_token() -> str:
    """A fresh, URL-safe challenge token (shown once, never stored in plaintext)."""
    return secrets.token_urlsafe(24)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def challenge_keyword(token: str) -> str:
    """The keyword the publisher must add to a published version's keywords."""
    return f"{CHALLENGE_KEYWORD_PREFIX}{token}"


def _keywords_from_registry_data(registry: str, data: dict) -> list[str]:
    """Extract the LATEST published version's keywords from npm/PyPI data.

    npm packument: versions[dist-tags.latest].keywords (list).
    PyPI project json: info.keywords (comma/space-separated string).
    Pure — the caller supplies the already-fetched data.
    """
    if registry == "npm":
        latest = ((data.get("dist-tags") or {}).get("latest")) if data else None
        ver = (data.get("versions") or {}).get(latest) if latest else None
        kw = (ver or {}).get("keywords") or []
        return [str(k) for k in kw if isinstance(k, str)]
    # pypi
    raw = ((data.get("info") or {}).get("keywords") or "") if data else ""
    return [k.strip() for k in raw.replace(",", " ").split() if k.strip()]


def find_challenge_match(registry: str, data: dict, token_hash: str) -> dict:
    """Scan the fetched registry data for a challenge keyword whose token hashes
    to ``token_hash``. Returns {found: bool, version: str|None, keyword: str|None}.
    Pure: no I/O — the caller fetches the data."""
    keywords = _keywords_from_registry_data(registry, data)
    version = None
    if registry == "npm" and data:
        version = (data.get("dist-tags") or {}).get("latest")
    elif data:
        version = (data.get("info") or {}).get("version")
    for kw in keywords:
        if kw.startswith(CHALLENGE_KEYWORD_PREFIX):
            token = kw[len(CHALLENGE_KEYWORD_PREFIX) :]
            if token and hash_token(token) == token_hash:
                return {"found": True, "version": version, "keyword": kw}
    return {"found": False, "version": version, "keyword": None}


# Methods that constitute an AUTOMATED, unforgeable strong proof. Defined now,
# produced later (2b-2+). manual_admin is deliberately NOT here — an admin
# attestation is the review path, not an automated proof.
STRONG_AUTO_METHODS: frozenset[str] = frozenset(
    {
        "publish_challenge",  # 2b-2 (first) — a token in a published version
        "npm_provenance_repo_control",  # 2b-3
        "pypi_trusted_publishing_repo_control",  # 2b-4
        "verified_maintainer",  # later
    }
)

# Forgeable registry-metadata signals — a Medium signal (review priority), never
# strong. Populated when 2a-style metadata matching feeds ownership (not now).
MEDIUM_METHODS: frozenset[str] = frozenset({"registry_metadata_match"})

# The admin attestation method that exists today (verified => admin CAN publish
# via the review path, but it is NOT auto-eligible).
ADMIN_METHOD = "manual_admin"


def method_confidence(method: str | None) -> str:
    if method in STRONG_AUTO_METHODS:
        return STRONG
    if method == ADMIN_METHOD:
        return ADMIN_ATTESTED
    if method in MEDIUM_METHODS:
        return MEDIUM
    if method:
        return WEAK
    return NONE


def derive_ownership_evidence(
    method: str | None, effective_status: str, *, evidence: dict | None = None
) -> dict:
    """Derive the ownership evidence for the auto gate from a claim's method and
    its EFFECTIVE status (verified | revoked | expired | missing | ...).

    ``auto_eligible`` is True ONLY for a STRONG, currently-verified proof. Admin
    attestation, medium signals, weak claims, and missing/expired/revoked claims
    are never auto-eligible — they route to review.
    """
    confidence = method_confidence(method)
    verified = effective_status == "verified"
    auto_eligible = confidence == STRONG and verified

    if auto_eligible:
        reason = ""
    elif confidence == STRONG and not verified:
        reason = f"strong proof present but claim status is '{effective_status}'"
    elif confidence == ADMIN_ATTESTED:
        reason = "admin attestation only — not an automated ownership proof"
    elif confidence == MEDIUM:
        reason = "registry metadata matches but is forgeable — not a strong proof"
    elif confidence == WEAK:
        reason = "weak/unverified ownership signal"
    else:
        reason = "no ownership proof on record"

    return {
        "confidence": confidence,
        "auto_eligible": auto_eligible,
        "method": method,
        "status": effective_status,
        "reason": reason,
        "evidence": evidence or {},
    }
