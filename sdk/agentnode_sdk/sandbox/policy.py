"""Trust-tier sandbox policy: who may run on the host, who needs isolation.

This is the live, fail-closed enforcement point. A trust label alone never grants
host execution — only the explicit tier rule does. ``require_sandbox_for_tier`` is
the pure, unit-tested core; ``enforce_sandbox_policy`` is the thin live wrapper the
run path calls.
"""
from __future__ import annotations

import logging

from agentnode_sdk.sandbox.backend import SandboxBackend
from agentnode_sdk.sandbox.container_backend import ContainerBackend
from agentnode_sdk.sandbox.types import SandboxAvailability, SandboxRequiredError

logger = logging.getLogger("agentnode.sandbox")

# curated/system: AgentNode-owned, may run on the host.
_HOST_ALLOWED_TIERS = {"curated"}
# trusted third-party: P0.1 temporary transition — allowed on host but warned.
# Target state: trusted third-party packages run sandboxed too.
_HOST_TOLERATED_TIERS = {"trusted"}

_UNAVAILABLE = SandboxAvailability(available=False, backend="none", reason="")

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
    runtime_hint: str = "",
    backend: SandboxBackend | None = None,
) -> None:
    """Live wrapper for the run path. Probes the cached default backend only for
    sandbox-required tiers (host-allowed/tolerated tiers short-circuit without a
    runtime probe). Raises SandboxRequiredError on fail-closed."""
    tier = (trust_level or "").lower()
    if tier in _HOST_ALLOWED_TIERS or tier in _HOST_TOLERATED_TIERS:
        require_sandbox_for_tier(trust_level, _UNAVAILABLE)  # delegates (handles warning)
        return
    be = backend or get_default_backend()
    require_sandbox_for_tier(trust_level, be.check_available())
