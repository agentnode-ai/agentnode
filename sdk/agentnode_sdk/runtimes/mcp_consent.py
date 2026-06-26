"""Consent-identity + refusal scaffold for credentialed community MCPs (Stage 3A).

INERT / SCAFFOLD ONLY. This module computes a stable per-identity *consent key* and
produces value-free refusal reasons. It does NOT read, hold, or inject any secret, does
NOT start an egress proxy, does NOT prompt, and does NOT persist anything. In Stage 3A
there is NO allowed path: a credentialed community MCP is ALWAYS refused — ``refusal_reason``
can only ever return a refusal string (never "" / "allowed"). Real consent prompts + key
injection are Stage 3B, gated behind Stage 4 (pre-install/sealed volume) + Stage 5
(verified ``allowed_domains``).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

# Value-free refusal reason codes (no secrets, safe for logs/errors/audit).
REASON_NO_DOMAINS = "missing_or_invalid_allowed_domains"
REASON_PENDING = "credentialed_mcp_unsupported_pending_stage4"


@dataclass(frozen=True)
class ConsentIdentity:
    """The exact thing a future consent binds to. Names + domains only — never values."""
    slug: str
    version: str
    artifact_hash: str
    env_key_names: tuple[str, ...]      # NAMES only, sorted, de-duplicated
    allowed_domains: tuple[str, ...]    # sorted, de-duplicated


def _norm_names(items) -> tuple:
    """Env-var NAMES: sort + de-dup, but case-SENSITIVE (API_KEY != api_key)."""
    return tuple(sorted({str(x) for x in (items or [])}))


def _norm_domains(items) -> tuple:
    """Hostnames: lowercase + strip trailing dot, then sort + de-dup — so write
    variants (API.GITHUB.COM, api.github.com.) collapse to one canonical form and
    bind the same consent key. Pure string work; no validation, no I/O."""
    return tuple(sorted({str(x).strip().lower().rstrip(".") for x in (items or [])}))


def build_consent_identity(
    slug: str,
    version: str,
    artifact_hash: str,
    env_key_names,
    allowed_domains,
) -> ConsentIdentity:
    """Normalize inputs into a ConsentIdentity. Pure — no I/O, no secrets.

    ``env_key_names`` are env-var NAMES (never values), sorted + de-duped, case-sensitive.
    ``allowed_domains`` are canonicalized (lowercase, no trailing dot) + sorted + de-duped
    so casing/trailing-dot variants do not produce different consent keys.
    """
    return ConsentIdentity(
        slug=str(slug),
        version=str(version),
        artifact_hash=str(artifact_hash),
        env_key_names=_norm_names(env_key_names),
        allowed_domains=_norm_domains(allowed_domains),
    )


def consent_key(identity: ConsentIdentity) -> str:
    """Stable, order-independent hash binding the full identity.

    Differs whenever slug / version / artifact_hash / env_key_names / allowed_domains
    differ — so consent can never silently transfer to another MCP, version, artifact,
    key-set, or domain-set.
    """
    canonical = json.dumps(
        [
            identity.slug,
            identity.version,
            identity.artifact_hash,
            list(identity.env_key_names),
            list(identity.allowed_domains),
        ],
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def redact_env_keys(env_key_names) -> str:
    """'GITHUB_TOKEN, SLACK_TOKEN (2 keys)' — NAMES + count only, for prompts/audit.

    Takes env-var NAMES; never receives or emits a secret value.
    """
    names = _norm_names(env_key_names)
    n = len(names)
    label = "key" if n == 1 else "keys"
    listing = ", ".join(names) if names else "(none)"
    return f"{listing} ({n} {label})"


class CredentialedMcpRefused(RuntimeError):
    """A credentialed community MCP cannot run (fail-closed). Carries a value-free
    ``reason`` code — never a secret."""

    def __init__(self, reason: str, message: str | None = None):
        self.reason = reason
        super().__init__(message or reason)


def refusal_reason(*, allowed_domains_ok: bool = True) -> str:
    """Pick the precise, value-free refusal reason for a credentialed community MCP.

    Stage 3A has NO allowed branch: this ALWAYS returns a refusal reason string. Even
    with valid domains the result is a refusal (pending Stage 4/5) — it can never be ''
    or 'allowed'.
    """
    if not allowed_domains_ok:
        return REASON_NO_DOMAINS
    return REASON_PENDING


# ---------------------------------------------------------------------------
# Stage 3B-1: consent resolver + grant binding (INERT — NOT driven from the live
# run path yet; credentialed MCP execution stays refused until 3B-2). Pure of
# docker / egress / secret reads. The resolver decides authorization; the runtime
# (mcp_runner.start) still raises CredentialedMcpRefused in 3B-1.
# ---------------------------------------------------------------------------

# Value-free resolver outcome codes (safe for logs/audit; never a secret).
REASON_GRANT_VALID = "grant_valid"
REASON_CONSENT_GRANTED = "consent_granted"
REASON_CONSENT_GRANTED_EPHEMERAL = "consent_granted_ephemeral"
REASON_NO_GRANT_NON_INTERACTIVE = "no_valid_grant_non_interactive"
REASON_CONSENT_REJECTED = "consent_rejected"


@dataclass(frozen=True)
class ConsentDecision:
    """Outcome of resolving consent for a credentialed MCP identity. ``authorized`` is the
    only field the (future 3B-2) gate keys on; ``reason`` is a value-free code; ``grant`` is
    the persisted metadata grant (or None for ephemeral / refusal). Carries no secret."""
    authorized: bool
    reason: str
    grant: dict | None = None


def build_identity_from_entry(slug: str, entry: dict | None) -> ConsentIdentity:
    """Pure: derive the ConsentIdentity from a lockfile entry. Binds to slug, version, the
    SEALED ``mcp_preinstall.artifact_hash`` (content-bound, NOT a manifest claim), the env-key
    NAMES (``mcp_env_keys``), and the SEALED ``mcp_allowed_domains``. No secret values, no
    host-env, no runtime output."""
    entry = entry or {}
    preinstall = entry.get("mcp_preinstall") or {}
    return build_consent_identity(
        slug=slug,
        version=entry.get("version", ""),
        artifact_hash=preinstall.get("artifact_hash", ""),
        env_key_names=entry.get("mcp_env_keys") or [],
        allowed_domains=entry.get("mcp_allowed_domains") or [],
    )


def _normalize_callback_result(res):
    """A consent callback may return ``approved: bool`` or ``(approved, lifetime)``. Default
    lifetime is 90d; 'forever' is honored ONLY if the callback explicitly returns it (never the
    default). Unknown lifetimes fall back to the default."""
    from agentnode_sdk.runtimes import mcp_consent_store as store
    if isinstance(res, tuple):
        approved = bool(res[0])
        lifetime = res[1] if len(res) > 1 and res[1] else store.DEFAULT_LIFETIME
    else:
        approved = bool(res)
        lifetime = store.DEFAULT_LIFETIME
    if lifetime not in store.ALL_LIFETIMES:
        lifetime = store.DEFAULT_LIFETIME
    return approved, lifetime


def resolve_consent(identity: ConsentIdentity, *, callback=None, now=None) -> ConsentDecision:
    """Decide whether a credentialed MCP identity is consent-authorized. PURE of docker /
    egress / secret reads.

    (1) A valid stored grant for the EXACT identity ⇒ authorized (works non-TTY = Q3=A).
    (2) Else, if a consent ``callback`` is present (TTY) ⇒ prompt; on approval persist a grant
        per the chosen lifetime (``this_run`` ephemeral ⇒ persist NOTHING) ⇒ authorized; on
        rejection ⇒ refused.
    (3) Else (non-TTY, no valid grant) ⇒ refused.

    NOTE (3B-1): this is exercised by tests + reserved for 3B-2. The live ``mcp_runner.start``
    does NOT call this yet and still refuses credentialed execution.
    """
    from agentnode_sdk.runtimes import mcp_consent_store as store
    ck = consent_key(identity)
    existing = store.find_valid(ck, now)
    if existing is not None:
        return ConsentDecision(True, REASON_GRANT_VALID, existing)
    if callback is None:
        return ConsentDecision(False, REASON_NO_GRANT_NON_INTERACTIVE, None)
    approved, lifetime = _normalize_callback_result(callback(identity))
    if not approved:
        return ConsentDecision(False, REASON_CONSENT_REJECTED, None)
    if lifetime == store.LIFETIME_THIS_RUN:
        return ConsentDecision(True, REASON_CONSENT_GRANTED_EPHEMERAL, None)  # not persisted
    grant = store.add(
        consent_key=ck, slug=identity.slug, version=identity.version,
        artifact_hash=identity.artifact_hash, env_key_names=identity.env_key_names,
        allowed_domains=identity.allowed_domains, lifetime=lifetime, now=now,
    )
    return ConsentDecision(True, REASON_CONSENT_GRANTED, grant)
