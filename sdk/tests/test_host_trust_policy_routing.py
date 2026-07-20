"""1B: the host-trust policy wired LIVE into the run chokepoints (toolpacks + MCPs).

Proves: ``default`` == today's behavior; ``curated_only``/``none`` route trusted
(and curated) into the sandbox; ``mode='direct'`` cannot bypass the policy; and the
config key validates + round-trips through save/load. No installer / lockfile
metadata / credentialed-MCP rework / doctor here — that is 1C/1D.
"""
from __future__ import annotations

import io
import json

import pytest

import agentnode_sdk.runtimes.python_runner as pr
from agentnode_sdk.config import host_trust_policy, load_config, save_config, set_value
from agentnode_sdk.runtimes import mcp_runner
from agentnode_sdk.runtimes.mcp_runner import MCPServerProcess
from agentnode_sdk.sandbox import SandboxRequiredError, set_default_backend
from agentnode_sdk.sandbox.backend import SandboxBackend
from agentnode_sdk.sandbox.container_backend import ContainerBackend
from agentnode_sdk.sandbox.policy import enforce_sandbox_policy
from agentnode_sdk.sandbox.types import SandboxAvailability
from tests.hostpolicy import decision, plan

MCP_CMD = ["npx", "-y", "@scope/some-mcp@1.2.3"]


# ===========================================================================
# Config key: validation + round-trip (survives _merge_defaults on load)
# ===========================================================================

@pytest.fixture
def _cfgdir(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path))
    return tmp_path


def test_host_trust_policy_defaults_to_default(_cfgdir):
    assert host_trust_policy() == "default"


def test_host_trust_policy_roundtrips_through_save_load(_cfgdir):
    save_config(set_value(load_config(), "sandbox.host_trust_policy", "curated_only"))
    assert host_trust_policy() == "curated_only"      # survives merge on reload
    save_config(set_value(load_config(), "sandbox.host_trust_policy", "none"))
    assert host_trust_policy() == "none"


@pytest.mark.parametrize("bad", ["curated-only", "strict", "trusted", "off", ""])
def test_set_value_rejects_typos(_cfgdir, bad):
    with pytest.raises(ValueError):
        set_value(load_config(), "sandbox.host_trust_policy", bad)


# ===========================================================================
# Toolpack routing — via RunToolResult.mode_used
#   "sandbox" = container path ; "subprocess"/"direct" = host path
# ===========================================================================

def _route_toolpack(monkeypatch, policy, trust, mode="auto"):
    from tests.hostpolicy import decision
    monkeypatch.setattr(pr, "_run_container", lambda *a, **k: ("C", None, False))
    monkeypatch.setattr(pr, "_run_subprocess", lambda *a, **k: ("S", None, False))
    monkeypatch.setattr(pr, "_run_direct", lambda *a, **k: "D")
    monkeypatch.setattr(pr, "_get_trust_level", lambda *a, **k: trust)
    # F1: run_python consumes the owner's decision (built here from policy+trust),
    # never re-reads host_trust_policy.
    return pr.run_python("p", "do", entry={"trust_level": trust}, mode=mode,
                         _host_policy_decision=decision(trust, policy)).mode_used


@pytest.mark.parametrize("policy,trust,expected", [
    # default == today's behavior (the regression guarantee)
    ("default", "curated", "subprocess"),
    ("default", "trusted", "subprocess"),
    ("default", "verified", "sandbox"),
    ("default", "unverified", "sandbox"),
    ("default", None, "sandbox"),
    # curated_only: trusted joins the sandbox, curated stays host
    ("curated_only", "curated", "subprocess"),
    ("curated_only", "trusted", "sandbox"),
    ("curated_only", "verified", "sandbox"),
    # none: nobody runs on the host
    ("none", "curated", "sandbox"),
    ("none", "trusted", "sandbox"),
    ("none", "verified", "sandbox"),
])
def test_toolpack_routing_matrix(monkeypatch, policy, trust, expected):
    assert _route_toolpack(monkeypatch, policy, trust) == expected


def test_direct_mode_cannot_bypass_policy(monkeypatch):
    # under default, mode='direct' for trusted is honored (host, direct)
    assert _route_toolpack(monkeypatch, "default", "trusted", mode="direct") == "direct"
    # under curated_only, mode='direct' for trusted is IGNORED — sandbox wins
    # (the requires_sandbox check runs BEFORE mode resolution)
    assert _route_toolpack(monkeypatch, "curated_only", "trusted", mode="direct") == "sandbox"
    # community can never escape to direct, under any policy
    assert _route_toolpack(monkeypatch, "default", "verified", mode="direct") == "sandbox"


# ===========================================================================
# MCP routing — host raw command vs container branch
# ===========================================================================

class _FakePopen:
    instances: list = []

    def __init__(self, args, **kwargs):
        self.args = args
        self.env = kwargs.get("env")
        self.stdin = io.StringIO()
        self.stdout = io.StringIO(
            json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}) + "\n")
        self.stderr = io.StringIO()
        self._alive = True
        _FakePopen.instances.append(self)

    def poll(self):
        return None if self._alive else 0

    def wait(self, timeout=None):
        self._alive = False
        return 0

    def kill(self):
        self._alive = False


@pytest.fixture(autouse=True)
def _mcp_isolate(monkeypatch):
    _FakePopen.instances = []
    monkeypatch.setattr(mcp_runner.subprocess, "Popen", _FakePopen)
    yield
    set_default_backend(None)


def _container_available(monkeypatch):
    be = ContainerBackend(runtime="docker")
    monkeypatch.setattr(be, "check_available", lambda: SandboxAvailability(
        available=True, backend="docker", reason="", daemon_ok=True, image_available=True))
    set_default_backend(be)


def _set_policy(monkeypatch, policy):
    monkeypatch.setattr("agentnode_sdk.config.host_trust_policy", lambda: policy)


def test_default_trusted_mcp_runs_on_host(monkeypatch):
    _container_available(monkeypatch)
    _set_policy(monkeypatch, "default")
    monkeypatch.setenv("HOME", "/home/realuser")
    MCPServerProcess("m", MCP_CMD, trust_level="trusted").start(_host_policy_decision=decision("trusted", "default"), launch_plan=plan("m", decision("trusted", "default"), {"mcp_command": list(MCP_CMD)}))
    assert _FakePopen.instances[-1].args == MCP_CMD          # raw command on host — today's behavior


def test_default_curated_mcp_runs_on_host(monkeypatch):
    _container_available(monkeypatch)
    _set_policy(monkeypatch, "default")
    MCPServerProcess("m", MCP_CMD, trust_level="curated").start(_host_policy_decision=decision("curated", "default"), launch_plan=plan("m", decision("curated", "default"), {"mcp_command": list(MCP_CMD)}))
    assert _FakePopen.instances[-1].args == MCP_CMD          # regression


def test_curated_only_trusted_mcp_is_sandboxed(monkeypatch):
    _container_available(monkeypatch)
    _set_policy(monkeypatch, "curated_only")
    # trusted now routes into the container path; a non-preinstalled npx MCP is refused (0.17)
    with pytest.raises(SandboxRequiredError, match="not preinstalled"):
        MCPServerProcess("m", MCP_CMD, trust_level="trusted").start(_host_policy_decision=decision("trusted", "curated_only"), launch_plan=plan("m", decision("trusted", "curated_only")))
    assert _FakePopen.instances == []                        # never launched on host


def test_none_curated_mcp_is_sandboxed(monkeypatch):
    _container_available(monkeypatch)
    _set_policy(monkeypatch, "none")
    with pytest.raises(SandboxRequiredError):
        MCPServerProcess("m", MCP_CMD, trust_level="curated").start(_host_policy_decision=decision("curated", "none"), launch_plan=plan("m", decision("curated", "none")))
    assert _FakePopen.instances == []


# ===========================================================================
# 1C: enforce early-fail polish + policy-specific MCP refusal message
# ===========================================================================

class _UnavailBackend(SandboxBackend):
    def check_available(self):
        return SandboxAvailability(available=False, backend="none", reason="no runtime")

    def wrap_command(self, spec):  # pragma: no cover
        raise AssertionError("must not run without a sandbox")


def test_enforce_trusted_under_curated_only_fails_closed_without_runtime(monkeypatch):
    _set_policy(monkeypatch, "curated_only")
    with pytest.raises(SandboxRequiredError, match="host_trust_policy=curated_only"):
        enforce_sandbox_policy("trusted", host_policy="curated_only", runtime_hint="mcp", backend=_UnavailBackend())


def test_enforce_trusted_under_default_allows_host_without_runtime(monkeypatch):
    _set_policy(monkeypatch, "default")
    # under default, trusted stays host-tolerated → no runtime required, no raise
    enforce_sandbox_policy("trusted", host_policy="default", runtime_hint="mcp", backend=_UnavailBackend())


def test_curated_only_mcp_refusal_names_the_policy(monkeypatch):
    _container_available(monkeypatch)
    _set_policy(monkeypatch, "curated_only")
    # F1 amendment: a non-preinstalled trusted MCP under curated_only is refused
    # fail-closed at PLAN BUILD ("not preinstalled") before start.
    with pytest.raises(SandboxRequiredError, match="not preinstalled"):
        MCPServerProcess("m", MCP_CMD, trust_level="trusted").start(_host_policy_decision=decision("trusted", "curated_only"), launch_plan=plan("m", decision("trusted", "curated_only")))
