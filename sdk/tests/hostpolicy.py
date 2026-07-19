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
