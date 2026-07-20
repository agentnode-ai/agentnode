"""MCP run routing + network isolation at MCPServerProcess.start().

Closes the audit-found gap: run_tool was not the complete MCP chokepoint
(cli/mcp_commands.py starts MCPServerProcess directly). Enforcement+routing now
live in start(), covering the agent path AND direct/CLI use.

MCP net-isolation (Fallback C): a community MCP runs ONLY when pinned + sealed +
network-isolated/allowlisted. A non-preinstalled community MCP (floating npx/uvx
runtime fetch) is REFUSED fail-closed — never an open ``network="default"``. The
preinstalled container mechanics (clean env, egress allowlist, stop) live in
test_mcp_preinstall_run.py. No real containers run here: the backend is
forced-available and subprocess.Popen is mocked.
"""
import io
import json

import pytest

from agentnode_sdk.runtimes import mcp_runner
from agentnode_sdk.runtimes.mcp_runner import MCPServerProcess
from tests.hostpolicy import decision, plan
from agentnode_sdk.sandbox import SandboxRequiredError, set_default_backend
from agentnode_sdk.sandbox.backend import SandboxBackend
from agentnode_sdk.sandbox.container_backend import ContainerBackend
from agentnode_sdk.sandbox.types import SandboxAvailability

MCP_CMD = ["npx", "-y", "@scope/some-mcp@1.2.3"]


class _FakePopen:
    """Captures the launch argv + env and simulates a successful MCP handshake."""
    instances: list["_FakePopen"] = []

    def __init__(self, args, **kwargs):
        self.args = args
        self.env = kwargs.get("env")
        self.stdin = io.StringIO()
        self.stdout = io.StringIO(
            json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}) + "\n"
        )
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
def _isolate(monkeypatch):
    _FakePopen.instances = []
    monkeypatch.setattr(mcp_runner.subprocess, "Popen", _FakePopen)
    yield
    set_default_backend(None)


def _use_available_container(monkeypatch):
    """Real ContainerBackend (so wrap_command produces the real hardened argv),
    forced available. Set in-body so it wins over the conftest global fixture."""
    be = ContainerBackend(runtime="docker")
    monkeypatch.setattr(be, "check_available", lambda: SandboxAvailability(
        available=True, backend="docker", reason="", daemon_ok=True, image_available=True))
    set_default_backend(be)
    return be


def _use_unavailable(monkeypatch):
    class _Un(SandboxBackend):
        def check_available(self):
            return SandboxAvailability(available=False, backend="none",
                                       reason="no container runtime found")
        def wrap_command(self, spec):
            raise SandboxRequiredError("no sandbox")
    set_default_backend(_Un())


# --- routing / bypass closure -------------------------------------------------

def test_non_preinstalled_community_mcp_refused(monkeypatch):
    """THE headline (Fallback C): a non-preinstalled community MCP would need an open-network
    runtime fetch (npx/uvx) — now REFUSED fail-closed, never containerized with open network."""
    _use_available_container(monkeypatch)
    with pytest.raises(SandboxRequiredError, match="not preinstalled"):
        MCPServerProcess("some-mcp", MCP_CMD, trust_level="verified").start(_host_policy_decision=decision("verified"), launch_plan=plan("m", decision("verified")))
    assert _FakePopen.instances == []            # never launched


def test_direct_construction_non_preinstalled_refused_not_host(monkeypatch):
    """The CLI-doctor pattern: a non-preinstalled community MCP is refused (never run on the
    host, never open network)."""
    _use_available_container(monkeypatch)
    with pytest.raises(SandboxRequiredError):
        MCPServerProcess("doctor-mcp", MCP_CMD, trust_level="verified").start(_host_policy_decision=decision("verified"), launch_plan=plan("m", decision("verified")))
    assert _FakePopen.instances == []


def test_missing_trust_level_non_preinstalled_refused(monkeypatch):
    """No trust_level + non-preinstalled -> refused (sandbox-required, never host, never open)."""
    _use_available_container(monkeypatch)
    with pytest.raises(SandboxRequiredError):
        MCPServerProcess("unknown-mcp", MCP_CMD).start(_host_policy_decision=decision(None), launch_plan=plan("m", decision(None)))  # no trust_level
    assert _FakePopen.instances == []


def test_missing_trust_level_blocked_when_unavailable(monkeypatch):
    _use_unavailable(monkeypatch)
    with pytest.raises(SandboxRequiredError):
        MCPServerProcess("unknown-mcp", MCP_CMD).start(_host_policy_decision=decision(None), launch_plan=plan("m", decision(None)))  # no trust_level
    assert _FakePopen.instances == []  # never launched


def test_community_mcp_with_env_keys_is_blocked(monkeypatch):
    _use_available_container(monkeypatch)
    server = MCPServerProcess("secret-mcp", MCP_CMD, trust_level="verified")
    # F1 amendment: a non-preinstalled sandbox MCP (even credentialed) is refused
    # fail-closed at PLAN BUILD, before start/egress/secret.
    with pytest.raises(SandboxRequiredError, match="not preinstalled"):
        server.start(_host_policy_decision=decision("verified"), launch_plan=plan("m", decision("verified")), env_keys=["OPENAI_API_KEY"])
    assert _FakePopen.instances == []  # never launched with secrets


def test_community_mcp_with_env_keys_refusal_is_precise_and_safe(monkeypatch):
    """Stage 3B-2b: a credentialed but NON-preinstalled community MCP fails closed with
    CredentialedMcpRefused + the precise reason `credentialed_requires_preinstall`,
    refused BEFORE reading the secret value and BEFORE starting egress (no runtime
    registry-fetch with a secret)."""
    import agentnode_sdk.sandbox.egress as egress
    _use_available_container(monkeypatch)
    # If any code reads the key VALUE from the environment, this sentinel would surface.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-SENTINEL-MUST-NOT-BE-READ")
    # The egress proxy must NOT be started in the refusal path.
    called = []
    monkeypatch.setattr(egress, "start_egress_proxy", lambda *a, **k: called.append((a, k)))

    # No preinstall intent ⇒ refused fail-closed at PLAN BUILD (before start/consent/egress/secret).
    server = MCPServerProcess("secret-mcp", MCP_CMD, trust_level="verified")
    with pytest.raises(SandboxRequiredError, match="not preinstalled") as ei:
        server.start(_host_policy_decision=decision("verified"), launch_plan=plan("m", decision("verified")), env_keys=["OPENAI_API_KEY"])

    assert "sk-SENTINEL-MUST-NOT-BE-READ" not in str(ei.value)  # VALUE never appears
    assert _FakePopen.instances == []              # no container launched with a key
    assert called == []                            # no egress proxy started


def test_no_runtime_is_fail_closed(monkeypatch):
    _use_unavailable(monkeypatch)
    with pytest.raises(SandboxRequiredError):
        MCPServerProcess("some-mcp", MCP_CMD, trust_level="verified").start(_host_policy_decision=decision("verified"), launch_plan=plan("m", decision("verified")))
    assert _FakePopen.instances == []


# --- host path for vetted tiers ----------------------------------------------

def test_curated_mcp_runs_on_host(monkeypatch):
    """curated stays on the host path (existing _mcp_env + raw command)."""
    monkeypatch.setenv("HOME", "/home/realuser")
    MCPServerProcess("curated-mcp", MCP_CMD, trust_level="curated").start(_host_policy_decision=decision("curated"), launch_plan=plan("m", decision("curated"), {"mcp_command": list(MCP_CMD)}))
    argv = _FakePopen.instances[-1].args
    assert argv == MCP_CMD                        # raw command, not docker
    assert argv[0] != "docker"
    # host path uses _mcp_env -> the host HOME is present for the (vetted) process
    assert _FakePopen.instances[-1].env.get("HOME") == "/home/realuser"
