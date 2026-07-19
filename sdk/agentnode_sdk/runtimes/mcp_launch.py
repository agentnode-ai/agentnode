"""Import-neutral MCP process launch identity + compatibility fingerprints (F1).

Pure data + deterministic fingerprints used to decide whether a POOLED
(non-credentialed) MCP process may be reused. Reuse requires not only the same
host/sandbox boundary but the full non-sensitive launch identity — command/argv,
package version, artifact_hash, trust level, transport, env-key NAMES — and, for
a sandbox process, the profile (image digest, network mode, sealed egress
domains, mounts). A slug is NOT a stable identity: install/upgrade/reinstall/
reseal/trust-refresh can change command/version/artifact/trust/profile under the
same slug while a healthy pooled process keeps running.

This is NOT runtime artifact verification (A3): ``artifact_hash`` is used only as
an identity value; the installed files are never re-hashed here, and disk
integrity is not proven. A3 (re-verify host files / bind ``module.__file__`` /
shadowing) stays a separate future arc.

No side effects: building a plan/fingerprint starts no process/container, writes
no volume, creates no egress resource, and reads no lockfile or config. Physical
resources are created only later, after the pool decides to (re)start.

Determinism: fingerprints use a versioned canonical JSON payload (sorted keys,
explicit separators) + SHA-256 — never ``hash()``/``repr()``/unordered sets/
object addresses/env values. Order-sensitive data (argv) keeps its order;
semantically unordered data (domains, env-key names) is canonically sorted.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

COMPATIBILITY_VERSION = 1


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
    """Immutable launch plan built ONCE from the gated entry + policy decision, used
    for BOTH the compatibility check and (on start) the actual launch."""
    slug: str
    command: tuple[str, ...]
    env_key_names: tuple[str, ...]
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


def build_mcp_launch_plan(slug: str, entry: dict, decision: Any) -> MCPLaunchPlan:
    """PURE: build the immutable launch plan + full compatibility from the SAME
    gated entry and the host-trust ``decision``. No I/O, no probe, no resource
    creation. ``decision`` is a ``HostTrustPolicyDecision`` (boundary + trust)."""
    entry = entry or {}
    boundary = decision.execution_boundary  # "host" | "sandbox"
    command = tuple(entry.get("mcp_command") or [])
    env_key_names = tuple(sorted(entry.get("mcp_env_keys") or []))

    launch_fp = _canonical_sha256({
        "v": COMPATIBILITY_VERSION,
        "slug": slug,
        "runtime_kind": "mcp",
        "transport": "stdio",
        "command": list(command),               # order-sensitive
        "version": entry.get("version"),
        "artifact_hash": entry.get("artifact_hash"),  # identity value only (not re-hashed)
        "env_key_names": list(env_key_names),   # canonically sorted; NAMES only, never values
    })

    sandbox_fp: str | None = None
    if boundary == "sandbox":
        from agentnode_sdk.sandbox import sandbox_volume_name
        from agentnode_sdk.sandbox.container_backend import _BASE_IMAGE
        domains = _declared_egress_domains(entry)
        volume = sandbox_volume_name(slug, entry.get("version"), entry.get("artifact_hash"))
        # image digest + hardened flags are constant per SDK version (they never
        # discriminate two calls in one process); backend (docker/podman) is
        # intentionally excluded because determining it needs a runtime probe and
        # the plan build must stay side-effect-free. The variable sandbox identity
        # (image, network, domains, volume/mounts) is captured here.
        sandbox_fp = _canonical_sha256({
            "v": COMPATIBILITY_VERSION,
            "image_digest": _BASE_IMAGE,
            "network": "egress" if domains else "none",
            "allowed_domains": list(domains),   # canonically sorted
            "mounts": [[volume, "/install", "ro"]],
        })

    compat = MCPProcessCompatibility(
        execution_boundary=boundary,
        trust_level=decision.trust_level,
        runtime_kind="mcp",
        launch_fingerprint=launch_fp,
        sandbox_profile_fingerprint=sandbox_fp,
    )
    return MCPLaunchPlan(slug=slug, command=command, env_key_names=env_key_names, compatibility=compat)
