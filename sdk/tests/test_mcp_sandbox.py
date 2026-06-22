"""P0.2 — MCP runs route into the container at MCPServerProcess.start().

Closes the audit-found gap: run_tool was not the complete MCP chokepoint
(cli/mcp_commands.py starts MCPServerProcess directly). Enforcement+routing now
live in start(), covering the agent path AND direct/CLI use.

P0.2 isolates host-FS, HOME and secrets — NOT the network (npx/uvx fetch live).
No real containers run here: the backend is forced-available and subprocess.Popen
is mocked to capture the launch argv.
"""
import io
import json

import pytest

from agentnode_sdk.runtimes import mcp_runner
from agentnode_sdk.runtimes.mcp_runner import MCPServerProcess
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

def test_verified_mcp_routed_into_container(monkeypatch):
    """THE headline: verified MCP -> Popen gets docker argv, original npx only at end."""
    _use_available_container(monkeypatch)
    MCPServerProcess("some-mcp", MCP_CMD, trust_level="verified").start()

    argv = _FakePopen.instances[-1].args
    assert argv[0] == "docker"
    assert "--name" in argv and "agentnode-sandbox" in argv
    assert "--cap-drop=ALL" in argv and "--read-only" in argv
    assert argv[-3:] == MCP_CMD            # original command only at the very end
    assert argv[:3] != MCP_CMD             # not a direct host launch


def test_direct_construction_is_routed_closes_cli_doctor_bypass(monkeypatch):
    """Exactly the CLI-doctor pattern: MCPServerProcess(..., trust_level).start()."""
    _use_available_container(monkeypatch)
    MCPServerProcess("doctor-mcp", MCP_CMD, trust_level="verified").start()
    argv = _FakePopen.instances[-1].args
    assert argv[0] == "docker"                       # routed, not host
    assert argv[-3:] == MCP_CMD


def test_missing_trust_level_is_sandbox_required_not_host(monkeypatch):
    """No trust_level -> treated as sandbox-required (containerized), never host."""
    _use_available_container(monkeypatch)
    MCPServerProcess("unknown-mcp", MCP_CMD).start()  # no trust_level
    assert _FakePopen.instances[-1].args[0] == "docker"


def test_missing_trust_level_blocked_when_unavailable(monkeypatch):
    _use_unavailable(monkeypatch)
    with pytest.raises(SandboxRequiredError):
        MCPServerProcess("unknown-mcp", MCP_CMD).start()  # no trust_level
    assert _FakePopen.instances == []  # never launched


# --- host-FS / HOME / secret isolation ---------------------------------------

def test_container_env_is_clean_no_host_home(monkeypatch):
    _use_available_container(monkeypatch)
    monkeypatch.setenv("HOME", "/home/realuser")
    monkeypatch.setenv("APPDATA", r"C:\Users\realuser\AppData")
    MCPServerProcess("some-mcp", MCP_CMD, trust_level="verified").start()

    argv = _FakePopen.instances[-1].args
    joined = " ".join(argv)
    assert "HOME=/sandbox-home" in argv          # clean container HOME
    assert "/home/realuser" not in joined        # no host HOME
    assert "realuser" not in joined              # no host APPDATA/USERPROFILE
    assert ".agentnode" not in joined            # credential store never mounted
    assert "-v" not in argv                      # no workspace/host mount


def test_community_mcp_with_env_keys_is_blocked(monkeypatch):
    _use_available_container(monkeypatch)
    server = MCPServerProcess("secret-mcp", MCP_CMD, trust_level="verified")
    with pytest.raises(RuntimeError, match="credentials|secret"):
        server.start(env_keys=["OPENAI_API_KEY"])
    assert _FakePopen.instances == []  # never launched with secrets


def test_community_mcp_with_env_keys_refusal_is_precise_and_safe(monkeypatch):
    """Stage 3A: credentialed community MCP fails closed with CredentialedMcpRefused +
    a value-free reason, WITHOUT reading the secret value and WITHOUT starting egress."""
    import agentnode_sdk.sandbox.egress as egress
    from agentnode_sdk.runtimes.mcp_consent import CredentialedMcpRefused, REASON_PENDING

    _use_available_container(monkeypatch)
    # If any code reads the key VALUE from the environment, this sentinel would surface.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-SENTINEL-MUST-NOT-BE-READ")
    # The egress proxy must NOT be started in the refusal path.
    called = []
    monkeypatch.setattr(egress, "start_egress_proxy", lambda *a, **k: called.append((a, k)))

    server = MCPServerProcess("secret-mcp", MCP_CMD, trust_level="verified")
    with pytest.raises(CredentialedMcpRefused) as ei:
        server.start(env_keys=["OPENAI_API_KEY"])

    assert ei.value.reason == REASON_PENDING
    msg = str(ei.value)
    assert "OPENAI_API_KEY" in msg                 # NAME is fine to show
    assert "sk-SENTINEL-MUST-NOT-BE-READ" not in msg  # VALUE never appears
    assert _FakePopen.instances == []              # no container launched with a key
    assert called == []                            # no egress proxy started


def test_no_runtime_is_fail_closed(monkeypatch):
    _use_unavailable(monkeypatch)
    with pytest.raises(SandboxRequiredError):
        MCPServerProcess("some-mcp", MCP_CMD, trust_level="verified").start()
    assert _FakePopen.instances == []


# --- lifecycle ----------------------------------------------------------------

def test_stop_removes_container(monkeypatch):
    _use_available_container(monkeypatch)
    runs = []
    monkeypatch.setattr(mcp_runner.subprocess, "run",
                        lambda args, **k: runs.append(args))
    server = MCPServerProcess("some-mcp", MCP_CMD, trust_level="verified")
    server.start()
    name = server._container_name
    assert name and name.startswith("agentnode-mcp-")
    server.stop()
    assert ["docker", "rm", "-f", name] in runs


# --- host path for vetted tiers ----------------------------------------------

def test_curated_mcp_runs_on_host(monkeypatch):
    """curated stays on the host path (existing _mcp_env + raw command)."""
    monkeypatch.setenv("HOME", "/home/realuser")
    MCPServerProcess("curated-mcp", MCP_CMD, trust_level="curated").start()
    argv = _FakePopen.instances[-1].args
    assert argv == MCP_CMD                        # raw command, not docker
    assert argv[0] != "docker"
    # host path uses _mcp_env -> the host HOME is present for the (vetted) process
    assert _FakePopen.instances[-1].env.get("HOME") == "/home/realuser"
