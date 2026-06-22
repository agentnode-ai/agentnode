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
