"""Trust-tier sandbox policy: who may run on the host, who needs isolation.

This is the live, fail-closed enforcement point. A trust label alone never grants
host execution — only the explicit tier rule does. ``require_sandbox_for_tier`` is
the pure, unit-tested core; ``enforce_sandbox_policy`` is the thin live wrapper the
run path calls.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from agentnode_sdk.sandbox.backend import SandboxBackend
from agentnode_sdk.sandbox.container_backend import ContainerBackend
from agentnode_sdk.sandbox.types import SandboxAvailability, SandboxRequiredError

logger = logging.getLogger("agentnode.sandbox")


@dataclass(frozen=True)
class HostTrustPolicyDecision:
    """Immutable host-trust routing decision for ONE top-level execution.

    Produced ONLY by :func:`enforce_sandbox_policy` from an already-read, canonical
    host_trust_policy snapshot. Downstream runners consume it verbatim — they never
    re-read the policy, re-run :func:`requires_sandbox_for_policy`, re-implement the
    trust matrix, or reconstruct the boundary from a fresh config state.
    """
    policy: str
    trust_level: str
    sandbox_required: bool
    execution_boundary: Literal["host", "sandbox"]
    policy_recognized: bool = True

# curated/system: AgentNode-owned, may run on the host.
_HOST_ALLOWED_TIERS = {"curated"}
# trusted third-party: P0.1 temporary transition — allowed on host but warned.
# Target state: trusted third-party packages run sandboxed too.
_HOST_TOLERATED_TIERS = {"trusted"}

_UNAVAILABLE = SandboxAvailability(available=False, backend="none", reason="")

# Network permission allowlist — UNKNOWN = DENY. P0.3 turns the declared
# ``network_level`` permission into REAL container enforcement (--network none),
# not just a UI label. Only these explicitly-recognized "has network" levels
# (the vocabulary actually used across lock_integrity/validate/risk_profile)
# grant the container a network. Everything else — "none", missing, None, or any
# unrecognized value — is physically isolated. An unknown value is NEVER a
# silent network grant.
_NETWORK_GRANT_LEVELS = {
    "restricted", "internal", "external", "full", "unrestricted", "limited",
}


def network_for_level(network_level: str | None) -> str:
    """Map a declared ``network_level`` permission to a ProcessSpec network mode.

    Allowlist semantics (unknown = deny): a recognized network-granting level →
    ``"default"`` (network allowed); anything else (``none``/missing/``None``/
    unknown) → ``"none"`` (``--network none``, no socket).
    """
    return "default" if (network_level or "").strip().lower() in _NETWORK_GRANT_LEVELS else "none"

_default_backend: SandboxBackend | None = None


def get_default_backend() -> SandboxBackend:
    global _default_backend
    if _default_backend is None:
        _default_backend = ContainerBackend()
    return _default_backend


def set_default_backend(backend: SandboxBackend | None) -> None:
    """Test/integration seam: override (or reset with None) the default backend."""
    global _default_backend
    _default_backend = backend


def requires_sandbox(trust_level: str | None) -> bool:
    """True for tiers that must run inside a sandbox (everything not curated/
    trusted). Missing / None / unknown → True (never host) by construction."""
    tier = (trust_level or "").lower()
    return tier not in _HOST_ALLOWED_TIERS and tier not in _HOST_TOLERATED_TIERS


def host_allowed_tiers(policy: str | None) -> frozenset[str]:
    """Trust tiers allowed to run DIRECTLY ON THE HOST under a host-trust policy.

    Pure decision table (no config, no I/O). ``policy`` is the future user setting
    ``sandbox.host_trust_policy`` — wired into the run/install paths in a later
    step; here it is only the table, unit-tested in isolation.

      - ``"default"`` — curated + trusted may run on the host (today's behavior).
      - ``"curated_only"`` — only curated on the host; trusted is sandboxed.
      - ``"none"`` — nothing on the host; curated + trusted are sandboxed too.

    None / empty / unrecognized → ``"default"``: this table can therefore never
    *silently over-restrict* — narrowing host access happens only for an
    explicitly recognized stricter value. Validating the value (rejecting typos)
    is the config layer's job, not this pure core's.
    """
    p = (policy or "").strip().lower()
    if p == "curated_only":
        return frozenset(_HOST_ALLOWED_TIERS)
    if p == "none":
        return frozenset()
    return frozenset(_HOST_ALLOWED_TIERS | _HOST_TOLERATED_TIERS)


def requires_sandbox_for_policy(trust_level: str | None, policy: str | None) -> bool:
    """Pure: True if ``trust_level`` must be sandboxed under ``policy``.

    Missing / None / unknown trust → True (never host), the same fail-closed rule
    as :func:`requires_sandbox`. This is the policy-parameterized core; the
    existing :func:`requires_sandbox` is exactly the ``"default"`` special case,
    i.e. ``requires_sandbox(t) == requires_sandbox_for_policy(t, "default")`` for
    every ``t`` (asserted in the tests — 1A changes no current behavior).
    """
    tier = (trust_level or "").strip().lower()
    return tier not in host_allowed_tiers(policy)


def require_sandbox_for_tier(
    trust_level: str | None, availability: SandboxAvailability
) -> None:
    """Pure fail-closed gate.

    curated → host (no sandbox needed). trusted → host (P0.1 transition, warned).
    verified / unverified / unknown / None → sandbox required; raise
    SandboxRequiredError when none is available.
    """
    tier = (trust_level or "").lower()
    if tier in _HOST_ALLOWED_TIERS:
        return
    if tier in _HOST_TOLERATED_TIERS:
        # P0.1 temporary transition: trusted still allowed on host.
        # Target state: trusted third-party packages run sandboxed too.
        logger.warning(
            "trusted package allowed to run on the host (P0.1 transition); target "
            "state is sandboxed execution for trusted third-party packages too"
        )
        return
    if not availability.available:
        raise SandboxRequiredError(
            "Community package execution requires a container runtime (Docker or "
            f"Podman). {availability.reason or 'None detected'} — refusing to run "
            "untrusted code on the host."
        )
    # available: P0.1 only verifies availability; actual container routing is P0.2/P0.3.
    return


def enforce_sandbox_policy(
    trust_level: str | None,
    *,
    host_policy: str,
    runtime_hint: str = "",
    backend: SandboxBackend | None = None,
) -> HostTrustPolicyDecision:
    """Live gate for the run path. Takes an ALREADY-READ, canonical host-trust
    policy snapshot (``host_policy``) — it performs NO config read of its own.

    Computes the sandbox requirement ONCE from ``(trust_level, host_policy)`` and,
    for sandbox-required tiers, probes runtime availability (fail-closed:
    ``SandboxRequiredError`` when none). Host-allowed/tolerated tiers that run on
    the host short-circuit without a probe (and keep the trusted-transition
    warning). Returns the immutable :class:`HostTrustPolicyDecision` the owner
    threads to the concrete runner — nothing downstream recomputes it.
    """
    sandbox_required = requires_sandbox_for_policy(trust_level, host_policy)
    if sandbox_required:
        be = backend or get_default_backend()
        avail = be.check_available()
        if not avail.available:
            tier = (trust_level or "").lower()
            if tier in _HOST_ALLOWED_TIERS or tier in _HOST_TOLERATED_TIERS:
                raise SandboxRequiredError(
                    f"'{tier}' package must run sandboxed under "
                    f"sandbox.host_trust_policy={host_policy} but no container runtime is "
                    f"available. {avail.reason or 'No runtime detected'}."
                )
            raise SandboxRequiredError(
                "Community package execution requires a container runtime (Docker or "
                f"Podman). {avail.reason or 'None detected'} — refusing to run "
                "untrusted code on the host."
            )
    else:
        # Host-allowed/tolerated tier running on the host: preserve the trusted
        # transition warning (require_sandbox_for_tier warns for _HOST_TOLERATED,
        # returns silently for _HOST_ALLOWED; never raises for these tiers).
        require_sandbox_for_tier(trust_level, _UNAVAILABLE)
    return HostTrustPolicyDecision(
        policy=host_policy,
        trust_level=(trust_level or ""),
        sandbox_required=sandbox_required,
        execution_boundary="sandbox" if sandbox_required else "host",
    )
