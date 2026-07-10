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

# Confidence levels (derived, stored in the gate_result JSONB).
STRONG = "strong"
MEDIUM = "medium"
WEAK = "weak"
ADMIN_ATTESTED = "admin_attested"
NONE = "none"

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
