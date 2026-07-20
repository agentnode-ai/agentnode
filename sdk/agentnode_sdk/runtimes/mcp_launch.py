"""Import-neutral MCP process launch identity + compatibility fingerprints (F1).

The immutable :class:`MCPLaunchPlan` is built ONCE (pure, side-effect-free) from
the gated entry + host-trust decision and then drives BOTH the compatibility check
and the actual process start — so the fingerprint is provably the same definition
that is launched (no separate re-derivation in ``start()``). It carries the ACTUAL
per-boundary command (host: ``mcp_command``; sandbox: the validated preinstall
command — NOT ``mcp_command``), and, for a sandbox process, the full non-sensitive
profile (image digest, backend kind, network mode, sealed egress domains, volume/
mounts, profile version).

Reuse of a pooled (non-credentialed) MCP requires full compatibility equality:
slug alone is not identity — install/upgrade/reinstall/reseal/trust-refresh can
change command/version/artifact/trust/profile under the same slug.

NOT runtime artifact verification (A3): ``artifact_hash`` is an identity value
only (never re-hashed here); disk integrity / ``module.__file__`` binding /
shadowing stay a separate A3 arc.

No side effects: building a plan starts no process/container, writes no volume,
creates no egress resource, and reads no lockfile or config (the resolved backend
KIND is passed in by the owner). Determinism: fingerprints use a versioned
canonical JSON payload (sorted keys, explicit separators) + SHA-256 — never
``hash()``/``repr()``/unordered sets/object addresses/env values. Argv keeps its
order; domains and env-key names are canonically sorted.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

COMPATIBILITY_VERSION = 1
SANDBOX_PROFILE_VERSION = 1  # bump to force restarts when constant hardening changes


@dataclass(frozen=True)
class MCPProcessCompatibility:
    """Full compatibility identity of a pooled MCP process (non-credentialed)."""
    execution_boundary: Literal["host", "sandbox"]
    trust_level: str
    runtime_kind: Literal["mcp"]
    launch_fingerprint: str
    sandbox_profile_fingerprint: str | None


@dataclass(frozen=True)
class MCPLaunchPlan:
    """Immutable launch plan: built ONCE, drives BOTH the compatibility check and the
    actual start. ``start()`` consumes these fields for pooled MCPs — it does not
    re-resolve command/boundary/backend/network/domains/mounts/image/volume from the
    entry, config, or defaults."""
    slug: str
    boundary: Literal["host", "sandbox"]
    command: tuple[str, ...]           # the ACTUAL command start runs
    env_key_names: tuple[str, ...]
    # sandbox-only (None/empty on host):
    network: str                       # "none" | "egress" (sandbox); "" (host)
    allowed_domains: tuple[str, ...]
    volume: str | None
    artifact_hash: str | None
    manager: str | None
    image_digest: str | None
    backend_kind: str | None
    compatibility: MCPProcessCompatibility


def _canonical_sha256(payload: dict) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _declared_egress_domains(entry: dict) -> tuple[str, ...]:
    """Pure: canonical sealed egress domains for a sandbox MCP (empty ⇒ fully
    network-isolated). Mirrors ``_preinstalled_spec``'s declared-network choice
    WITHOUT starting a proxy or touching the network."""
    try:
        from agentnode_sdk.sandbox.domain_policy import canonicalize_allowed_domains
        sealed = canonicalize_allowed_domains((entry or {}).get("mcp_allowed_domains") or [])
    except Exception:
        return ()
    return tuple(sorted(sealed))


def _resolve_sandbox_identity(slug: str, entry: dict):
    """Pure, FAIL-CLOSED: the VALIDATED sandbox launch identity (command/volume/
    artifact_hash/manager) that ``start()`` will actually run.

    A sandbox MCP WITHOUT preinstall intent, or with an INVALID preinstall
    definition, is REFUSED HERE (at plan build, before any pool access or start) —
    NEVER a host-like ``mcp_command`` fallback, NEVER None-reduced fields, NEVER a
    swallowed error. Only validated preinstall values (the ``mcp_preinstall_command``
    from the sealed ``/install`` volume — NOT ``mcp_command``) flow into the
    fingerprint, the plan, the ProcessSpec, and the real start."""
    from agentnode_sdk.sandbox.mcp_preinstall import (
        PreinstallError,
        has_preinstall_intent,
        validate_preinstall_fields,
    )
    from agentnode_sdk.sandbox.types import SandboxRequiredError

    if not has_preinstall_intent(entry):
        raise SandboxRequiredError(
            f"MCP '{slug}' is not preinstalled — cannot run sandboxed. Reinstall it "
            "pinned (exact mcp_install version); unpinnable (floating npx/uvx) MCPs "
            "are refused."
        )
    try:
        pspec = validate_preinstall_fields(slug, entry.get("version"), entry)
    except PreinstallError as exc:
        # Known validation/format error → safe translation (NO raw paths / command /
        # hash / domains / mounts). An UNEXPECTED error is NOT caught here — it
        # propagates and fails closed upward (never a fallback, never a plan).
        raise SandboxRequiredError(
            "Invalid MCP preinstall configuration. Execution was denied before start."
        ) from exc
    return tuple(pspec.command), pspec.volume, pspec.artifact_hash, pspec.manager


def build_mcp_launch_plan(slug: str, entry: dict, decision: Any,
                          *, backend_kind: str | None = None) -> MCPLaunchPlan:
    """PURE: build the immutable launch plan (+ compatibility) from the SAME gated
    entry + decision (+ owner-resolved backend kind for sandbox). No I/O, no probe,
    no resource creation. ``decision`` is a ``HostTrustPolicyDecision``."""
    entry = entry or {}
    boundary = decision.execution_boundary
    env_key_names = tuple(sorted(entry.get("mcp_env_keys") or []))

    if boundary == "host":
        command = tuple(entry.get("mcp_command") or [])
        launch_fp = _canonical_sha256({
            "v": COMPATIBILITY_VERSION, "slug": slug, "runtime_kind": "mcp",
            "transport": "stdio", "boundary": "host",
            "command": list(command), "version": entry.get("version"),
            "artifact_hash": entry.get("artifact_hash"),
            "env_key_names": list(env_key_names),
        })
        compat = MCPProcessCompatibility("host", decision.trust_level, "mcp", launch_fp, None)
        return MCPLaunchPlan(slug, "host", command, env_key_names, "", (), None,
                             entry.get("artifact_hash"), None, None, None, compat)

    # sandbox
    from agentnode_sdk.sandbox.container_backend import _BASE_IMAGE
    command, volume, artifact_hash, manager = _resolve_sandbox_identity(slug, entry)
    domains = _declared_egress_domains(entry)
    network = "egress" if domains else "none"
    launch_fp = _canonical_sha256({
        "v": COMPATIBILITY_VERSION, "slug": slug, "runtime_kind": "mcp",
        "transport": "stdio", "boundary": "sandbox",
        "command": list(command), "version": entry.get("version"),
        "artifact_hash": artifact_hash, "env_key_names": list(env_key_names),
    })
    sandbox_fp = _canonical_sha256({
        "profile_version": SANDBOX_PROFILE_VERSION,
        "image_digest": _BASE_IMAGE,
        "backend_kind": backend_kind,
        "network": network,
        "allowed_domains": list(domains),
        "mounts": [[volume, "/install", "ro"]],
    })
    compat = MCPProcessCompatibility("sandbox", decision.trust_level, "mcp", launch_fp, sandbox_fp)
    return MCPLaunchPlan(slug, "sandbox", command, env_key_names, network, domains,
                         volume, artifact_hash, manager, _BASE_IMAGE, backend_kind, compat)
