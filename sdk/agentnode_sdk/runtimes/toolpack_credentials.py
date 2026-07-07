"""Credentialed toolpacks — declared ``env_requirements`` at run time.

A toolpack may declare the credentials it needs in its manifest::

    env_requirements:
      - name: AHREFS_API_KEY
        required: true
        description: Ahrefs API key

The declaration is sealed into the lockfile at install (integrity-covered).
This module is PURE of secret values: it only ever handles env-var NAMES and
checks presence (``name in os.environ``) — a value is never read here.
"""

from __future__ import annotations

import os


def declared_env_names(entry: dict | None) -> list[str]:
    """All declared credential NAMES (required + optional), sorted + de-duped."""
    reqs = (entry or {}).get("env_requirements") or []
    names = set()
    for r in reqs:
        if isinstance(r, dict) and r.get("name"):
            names.add(str(r["name"]))
    return sorted(names)


def required_env_names(entry: dict | None) -> list[str]:
    """Declared NAMES with ``required`` truthy (missing flag = required)."""
    reqs = (entry or {}).get("env_requirements") or []
    names = set()
    for r in reqs:
        if isinstance(r, dict) and r.get("name") and r.get("required", True):
            names.add(str(r["name"]))
    return sorted(names)


def missing_required_env(entry: dict | None) -> list[str]:
    """Required NAMES not present in the host environment (presence only —
    no value is read)."""
    return [n for n in required_env_names(entry) if n not in os.environ]


def missing_env_message(slug: str, missing: list[str]) -> str:
    """Actionable, value-free error for a run blocked on missing credentials."""
    keys = ", ".join(missing)
    plural = "s" if len(missing) != 1 else ""
    return (
        f"Toolpack '{slug}' requires the environment variable{plural} {keys} "
        f"(declared in its env_requirements). Set {'them' if plural else 'it'} "
        "in your environment and retry."
    )


# ---------------------------------------------------------------------------
# Slice B: credentialed sandbox runs — consent + sealed egress allowlist.
# Mirrors mcp_runner._credentialed_launch's fail-closed order. NO secret VALUE
# is ever read here: domains → consent → presence are all name-level checks;
# the value is read by the container runtime itself (name-only passthrough).
# ---------------------------------------------------------------------------


class CredentialedToolpackRefused(RuntimeError):
    """A credentialed toolpack cannot run (fail-closed). Carries a value-free
    ``reason`` code — never a secret."""

    def __init__(self, reason: str, message: str | None = None):
        self.reason = reason
        super().__init__(message or reason)


def build_identity_from_toolpack_entry(slug: str, entry: dict | None):
    """Consent identity for a credentialed toolpack, derived from SEALED lock
    fields only: slug, version, the content-bound artifact_hash (the same value
    the sandbox volume gate recomputes), the declared env-var NAMES, and the
    sealed permissions.allowed_domains. Consent can never silently transfer to
    another package, version, artifact, key-set, or domain-set."""
    from agentnode_sdk.runtimes.mcp_consent import build_consent_identity

    entry = entry or {}
    perms = entry.get("permissions") or {}
    return build_consent_identity(
        slug=slug,
        version=entry.get("version", ""),
        artifact_hash=entry.get("artifact_hash", ""),
        env_key_names=declared_env_names(entry),
        allowed_domains=perms.get("allowed_domains") or [],
    )


def prepare_credentialed_run(
    slug: str, entry: dict | None, consent_callback=None
) -> tuple[list[str], list[str]]:
    """Gate a credentialed toolpack sandbox run. STRICT fail-closed order:

      1. sealed ``permissions.allowed_domains`` must canonicalize non-empty —
         a secret never rides an open or unrestricted network
      2. consent: a valid stored grant for the EXACT identity, or a TTY
         callback approval; otherwise refused (non-TTY without grant = refuse)
      3. host-key PRESENCE (names only) — required keys were already gated
         upstream; optional keys are passed through only if present

    Returns ``(sealed_domains, passthrough_names)``. Raises
    ``CredentialedToolpackRefused`` with a value-free reason otherwise.
    """
    from agentnode_sdk.runtimes.mcp_consent import redact_env_keys, resolve_consent
    from agentnode_sdk.runtimes.mcp_consent_store import GrantStoreError
    from agentnode_sdk.sandbox.domain_policy import (
        DomainPolicyError,
        canonicalize_allowed_domains,
    )

    entry = entry or {}
    identity = build_identity_from_toolpack_entry(slug, entry)
    consented_names = list(identity.env_key_names)

    # 1. sealed allowlist non-empty + canonical-valid (NO value read)
    try:
        sealed = canonicalize_allowed_domains(
            (entry.get("permissions") or {}).get("allowed_domains") or []
        )
    except (DomainPolicyError, ValueError):
        sealed = ()
    if not sealed:
        raise CredentialedToolpackRefused(
            "missing_or_invalid_allowed_domains",
            f"Toolpack '{slug}' declares credentials "
            f"({redact_env_keys(consented_names)}) but no valid "
            "permissions.allowed_domains — refusing to inject secrets without "
            "an enforced egress allowlist.",
        )

    # 2. consent via stored grant or TTY callback (NO value read)
    try:
        decision = resolve_consent(identity, callback=consent_callback)
    except GrantStoreError as e:
        raise CredentialedToolpackRefused(
            "grant_store_unusable", f"consent grant store unusable: {e}"
        )
    if not decision.authorized:
        raise CredentialedToolpackRefused(
            decision.reason,
            f"Credentialed run of '{slug}' not authorized ({decision.reason}). "
            f"Keys: {redact_env_keys(consented_names)}.",
        )

    # 3. presence only — pass through the consented names that are set
    passthrough = [k for k in consented_names if k in os.environ]
    return list(sealed), passthrough
