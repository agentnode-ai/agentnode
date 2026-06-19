"""Sandbox value types: availability result, process spec, fail-closed error.

P0.1 — used by detection (`check_available`), the pure command wrapper
(`wrap_command`), and the fail-closed tier gate. No execution lives here.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SandboxAvailability:
    """Result of probing for a usable container runtime.

    ``available`` is the only field the fail-closed gate keys on; the rest are
    diagnostics for logs/UX.
    """
    available: bool
    backend: str                       # "docker" | "podman" | "none"
    reason: str                        # human-readable; "" when available
    executable_path: str | None = None
    daemon_ok: bool = False
    image_available: bool = False
    image_digest: str | None = None


@dataclass(frozen=True)
class MountSpec:
    src: str
    dst: str
    read_only: bool = True


@dataclass(frozen=True)
class EgressSpec:
    """Handle for ``network="egress"`` (Design A, PROVEN in Stage 0A): the container
    joins a pre-created Docker ``--internal`` network (no host/internet route) and can
    reach the outside ONLY through a dual-homed CONNECT proxy. Stage 1 models this
    INERTLY — it does NOT create the network or start the proxy (that is Stage 2).

    network_name: the pre-created ``--internal`` network the container attaches to —
                  this is the topological egress enforcement (NOT the proxy env).
    proxy_url:    e.g. ``"http://<proxy-alias>:8888"`` — set as HTTP(S)_PROXY so the
                  process routes through the proxy (advisory; not the security boundary).
    allowed_domains: informational/audit only in Stage 1 (the proxy enforces; the
                  manifest source + lockfile sealing land in Stage 5).
    """
    network_name: str
    proxy_url: str
    allowed_domains: tuple[str, ...] = ()


@dataclass
class ProcessSpec:
    """A request to run a command in isolation.

    Consumed by ``wrap_command`` (P0.1, pure argv construction) and later by
    ``run_process`` / ``run_mcp_process`` (P0.2/P0.3, not yet implemented).
    """
    command: list[str]
    network: str = "none"              # "none" | "restricted" | "default" | "egress"
    mounts: list[MountSpec] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    limits: dict[str, str] = field(default_factory=dict)
    clean_home: bool = True            # never inherit the host HOME
    interactive: bool = False          # keep stdin open (long-lived MCP stdio)
    name: str | None = None            # container name (for stop/cleanup)
    egress: EgressSpec | None = None   # required iff network=="egress" (Stage 1, inert)


class SandboxRequiredError(RuntimeError):
    """Raised when a trust tier needs a sandbox but none is available (fail-closed)."""
