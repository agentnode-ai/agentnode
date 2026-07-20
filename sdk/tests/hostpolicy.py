"""Test helper: build a valid HostTrustPolicyDecision for F1 (single-snapshot).

Internal runners (run_agent / run_python / run_mcp / MCPServerProcess.start /
MCPServerPool.get_or_start) now REQUIRE the immutable host-trust decision the
owner (run_tool / cmd_mcp_doctor) produces. Tests that call these internals
directly must supply one explicitly — there is no config fallback.
"""
from __future__ import annotations

from agentnode_sdk.sandbox.policy import HostTrustPolicyDecision, requires_sandbox_for_policy


def decision(trust_level: str | None, policy: str = "default") -> HostTrustPolicyDecision:
    """A valid decision as the owner's enforce_sandbox_policy would produce it."""
    tl = trust_level or ""
    sr = requires_sandbox_for_policy(tl, policy)
    return HostTrustPolicyDecision(
        policy=policy,
        trust_level=tl,
        sandbox_required=sr,
        execution_boundary="sandbox" if sr else "host",
    )


def plan(slug, decision_obj, entry=None, backend_kind="docker"):
    """Build the MCPLaunchPlan the pool/start now require (F1 amendment)."""
    from agentnode_sdk.runtimes.mcp_launch import build_mcp_launch_plan
    return build_mcp_launch_plan(slug, entry or {}, decision_obj, backend_kind=backend_kind)


def preinstalled_entry(slug="m", version="1.0", manager="npm", package="pkg",
                       pkg_version="1.0.0", ahash="sha256:" + "a" * 64,
                       command=None, domains=(), env_keys=(), trust_level="unverified"):
    """A VALID sandbox preinstall entry (validate_preinstall_fields passes) so a
    sandbox launch plan can be built. A sandbox plan can no longer be built from a
    non-preinstalled entry — that is fail-closed at plan build."""
    from agentnode_sdk.sandbox.container_backend import mcp_sandbox_volume_name
    cmd = command or (["node", "/install/bin/x"] if manager == "npm" else ["python", "/install/x.py"])
    e = {
        "trust_level": trust_level,
        "version": version,
        "mcp_preinstalled": True,
        "mcp_preinstall": {"manager": manager, "package": package,
                           "version": pkg_version, "artifact_hash": ahash},
        "mcp_sandbox_volume": mcp_sandbox_volume_name(slug, version, manager, package, pkg_version),
        "mcp_preinstall_command": list(cmd),
    }
    if env_keys:
        e["mcp_env_keys"] = list(env_keys)
    if domains:
        e["mcp_allowed_domains"] = list(domains)
    return e


def run_agent(slug, *, entry, _host_policy_decision=None, **kw):
    """Wrapped run_agent that injects the owner-supplied decision (default policy)."""
    from agentnode_sdk.runtimes.agent_runner import run_agent as _r
    if _host_policy_decision is None:
        _host_policy_decision = decision(entry.get("trust_level"))
    return _r(slug, entry=entry, _host_policy_decision=_host_policy_decision, **kw)


def run_python(*args, _host_policy_decision=None, entry=None, **kw):
    """Wrapped run_python that injects the owner-supplied decision (default policy)."""
    from agentnode_sdk.runtimes.python_runner import run_python as _r
    if _host_policy_decision is None:
        _host_policy_decision = decision((entry or {}).get("trust_level"))
    return _r(*args, entry=entry, _host_policy_decision=_host_policy_decision, **kw)


def run_mcp(*args, _host_policy_decision=None, entry=None, **kw):
    """Wrapped run_mcp that injects the owner-supplied decision (default policy)."""
    from agentnode_sdk.runtimes.mcp_runner import run_mcp as _r
    if _host_policy_decision is None:
        _host_policy_decision = decision((entry or {}).get("trust_level"))
    return _r(*args, entry=entry, _host_policy_decision=_host_policy_decision, **kw)
