"""Stage 4B: run preinstalled MCPs from the sealed volume (run-side).

No real containers: a real ContainerBackend yields the genuine hardened argv; its
run_process is replaced by a recorder that returns the verifier's canned HASH, the MCP
launch uses a fake Popen, and subprocess.run (volume inspect) is mocked. Fail-closed
everywhere — a preinstalled MCP that does not validate/verify is refused, never falling
back to the npx/uvx mcp_command path.
"""
from __future__ import annotations

import io
import json
from types import SimpleNamespace
from unittest import mock

import pytest

from agentnode_sdk import lock_integrity
from agentnode_sdk.runtimes import mcp_runner
from agentnode_sdk.runtimes.mcp_runner import MCPServerProcess
from agentnode_sdk.sandbox import sandbox_volume_name, set_default_backend  # noqa: F401
from agentnode_sdk.sandbox.container_backend import ContainerBackend, mcp_sandbox_volume_name
from agentnode_sdk.sandbox.mcp_preinstall import (
    PreinstallError,
    PreinstallSpec,
    validate_preinstall_command,
    validate_preinstall_fields,
)
from agentnode_sdk.sandbox.types import (
    EgressSpec,
    SandboxAvailability,
    SandboxRequiredError,
)

HEX = "a" * 64
SLUG = "some-mcp"
MCP_VERSION = "1.0"
NPX_CMD = ["npx", "-y", "@scope/some-mcp@1.2.3"]


def _volume(manager="npm", package="@scope/some-mcp", package_version="1.2.3"):
    return mcp_sandbox_volume_name(SLUG, MCP_VERSION, manager, package, package_version)


# ===========================================================================
# pure validators (no daemon, no containers)
# ===========================================================================

def test_validate_command_ok():
    assert validate_preinstall_command(["node", "/install/bin/x"]) == ["node", "/install/bin/x"]
    assert validate_preinstall_command(["python", "/install/bin/y"]) == ["python", "/install/bin/y"]


@pytest.mark.parametrize("cmd", [
    "node /install/bin/x",                       # shell string, not a list
    ["node"],                                    # too short
    ["node", "/install/bin/x", "--flag"],        # too long
    ["sh", "/install/bin/x"], ["bash", "/install/bin/x"],
    ["cmd", "/install/bin/x"], ["powershell", "/install/bin/x"],
    ["npx", "/install/bin/x"], ["uvx", "/install/bin/x"],
    ["npm", "/install/bin/x"], ["pip", "/install/bin/x"],
    ["python", "-c"], ["node", "-c"],            # -c / flag as path
    ["node", "/tmp/x"], ["python", "/etc/passwd"],  # host paths
    ["node", "/install/../etc/x"],               # .. traversal
    ["node", "/install/./etc/x"],                # . segment (normalization trick)
    ["node", "/install/bin/../x"],               # .. mid-path
    ["node", "/install/bin//x"],                 # empty segment
    ["node", "bin/x"],                           # relative
    ["node", "/install/bin/x; rm -rf /"],        # shell metachar
    ["node", "/install/bin\\x"],                 # backslash
    ["node", "/install/bin/x\n"],                # newline
    ["node", "/install/bin/x\r"],                # carriage return
    ["node", "/install/bin/x\0"],                # NUL
    ["node", 123],                               # non-str
])
def test_validate_command_rejects(cmd):
    with pytest.raises(PreinstallError):
        validate_preinstall_command(cmd)


def _good_entry(**over):
    e = {
        "version": MCP_VERSION,
        "mcp_preinstalled": True,
        "mcp_preinstall": {"manager": "npm", "package": "@scope/some-mcp",
                           "version": "1.2.3", "artifact_hash": "sha256:" + HEX},
        "mcp_sandbox_volume": _volume(),
        "mcp_preinstall_command": ["node", "/install/bin/some-mcp"],
    }
    e.update(over)
    return e


def test_validate_fields_ok_and_version_separation():
    spec = validate_preinstall_fields(SLUG, MCP_VERSION, _good_entry())
    assert isinstance(spec, PreinstallSpec)
    assert spec.manager == "npm" and spec.package == "@scope/some-mcp"
    assert spec.package_version == "1.2.3"          # NOT the MCP version (1.0)
    assert spec.volume == _volume()
    # volume name binds BOTH mcp_version and package_version distinctly
    assert _volume(package_version="1.2.3") != mcp_sandbox_volume_name(
        SLUG, "1.2.3", "npm", "@scope/some-mcp", MCP_VERSION)


@pytest.mark.parametrize("entry", [
    {"version": MCP_VERSION},                                     # not preinstalled
    _good_entry(mcp_preinstalled=False),
    _good_entry(mcp_preinstall={"manager": "npm", "package": "p", "version": "1.0.0"}),  # missing key
    _good_entry(mcp_preinstall={"manager": "npm", "package": "p", "version": "1.0.0",
                                "artifact_hash": "sha256:" + HEX, "extra": 1}),           # extra key
    _good_entry(mcp_preinstall={"manager": "cargo", "package": "p", "version": "1.0.0",
                                "artifact_hash": "sha256:" + HEX}),                       # bad manager
    _good_entry(mcp_preinstall={"manager": "npm", "package": "p", "version": "1.0.0",
                                "artifact_hash": "sha256:" + "A" * 64}),                  # uppercase hex
    _good_entry(mcp_preinstall={"manager": "npm", "package": "p", "version": "1.0.0",
                                "artifact_hash": "deadbeef"}),                            # bad format
    _good_entry(mcp_sandbox_volume="agentnode-mcp-wrong"),                                # volume mismatch
    _good_entry(mcp_preinstall_command=["npx", "-y", "p"]),                               # bad command
])
def test_validate_fields_rejects(entry):
    with pytest.raises(PreinstallError):
        validate_preinstall_fields(SLUG, MCP_VERSION, entry)


# ===========================================================================
# run-side integration (recording backend + fake Popen)
# ===========================================================================

class _FakePopen:
    instances: list = []

    def __init__(self, args, **kwargs):
        self.args = args
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


class _RunRecorder:
    """Stands in for backend.run_process (the verifier). Returns a configurable HASH."""

    def __init__(self, backend, hash_hex=HEX):
        self.backend = backend
        self.calls = []
        self.hash_hex = hash_hex

    def __call__(self, spec, input_text=None, timeout=120.0):
        self.calls.append(self.backend.wrap_command(spec))
        return (0, f"HASH:{self.hash_hex}\nBINS:some-mcp\n", "")

    @property
    def last_argv(self):
        return self.calls[-1]


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    _FakePopen.instances = []
    monkeypatch.setattr(mcp_runner.subprocess, "Popen", _FakePopen)
    yield
    set_default_backend(None)


def _backend(monkeypatch, available=True, hash_hex=HEX):
    be = ContainerBackend(runtime="docker")
    monkeypatch.setattr(be, "check_available", lambda: SandboxAvailability(
        available=available, backend="docker" if available else "none",
        reason="" if available else "no runtime", daemon_ok=available, image_available=available))
    rec = _RunRecorder(be, hash_hex=hash_hex)
    be.run_process = rec  # type: ignore[method-assign]
    set_default_backend(be)
    return be, rec


def _patch_inspect(monkeypatch, returncode=0):
    runs = []
    def _run(args, **k):
        runs.append(args)
        return mock.Mock(returncode=returncode, stdout=b"", stderr=b"")
    monkeypatch.setattr(mcp_runner.subprocess, "run", _run)
    return runs


def _sealed(entry):
    return lock_integrity.seal_entry(dict(entry))


def _start(monkeypatch, entry, env_keys=None):
    server = MCPServerProcess(SLUG, NPX_CMD, trust_level="verified", entry=entry)
    server.start(env_keys=env_keys)
    return server


def test_happy_path_runs_from_volume(monkeypatch):
    _, rec = _backend(monkeypatch)
    _patch_inspect(monkeypatch, returncode=0)
    entry = _sealed(_good_entry(permissions={"network_level": "none"}))

    _start(monkeypatch, entry)

    # verifier ran FIRST (network none, RO /install, python3 hasher)
    vargv = rec.last_argv
    assert "--network" in vargv and vargv[vargv.index("--network") + 1] == "none"
    assert f"{_volume()}:/install:ro" in vargv
    assert vargv[-3:] == ["python3", "-c", __import__(
        "agentnode_sdk.sandbox.mcp_preinstall", fromlist=["_MCP_HASH_PY"])._MCP_HASH_PY]

    # MCP container launched from the preinstall command, NOT npx/uvx/mcp_command
    margv = _FakePopen.instances[-1].args
    assert margv[0] == "docker"
    assert margv[-2:] == ["node", "/install/bin/some-mcp"]
    assert "npx" not in margv and "uvx" not in margv
    assert f"{_volume()}:/install:ro" in margv             # volume RO
    assert "--network" in margv and margv[margv.index("--network") + 1] == "none"
    assert "NODE_PATH=/install/lib/node_modules" in margv  # module resolution env only


def test_pypi_preinstall_uses_python_and_pythonpath(monkeypatch):
    _, rec = _backend(monkeypatch)
    _patch_inspect(monkeypatch)
    vol = _volume("pypi", "some-mcp", "2.0.0")
    entry = _sealed(_good_entry(
        mcp_preinstall={"manager": "pypi", "package": "some-mcp", "version": "2.0.0",
                        "artifact_hash": "sha256:" + HEX},
        mcp_sandbox_volume=vol,
        mcp_preinstall_command=["python", "/install/bin/some-mcp"],
        permissions={"network_level": "none"}))
    _start(monkeypatch, entry)
    margv = _FakePopen.instances[-1].args
    assert margv[-2:] == ["python", "/install/bin/some-mcp"]
    assert "PYTHONPATH=/install" in margv


def test_integrity_mismatch_refused_no_start(monkeypatch):
    _backend(monkeypatch)
    _patch_inspect(monkeypatch)
    entry = _sealed(_good_entry(permissions={"network_level": "none"}))
    entry["mcp_sandbox_volume"] = "agentnode-mcp-tampered"   # break the seal post-hoc
    with pytest.raises(PreinstallError):
        _start(monkeypatch, entry)
    assert _FakePopen.instances == []                        # MCP never started


def test_volume_missing_refused_and_no_create(monkeypatch):
    _backend(monkeypatch)
    runs = _patch_inspect(monkeypatch, returncode=1)         # inspect fails
    entry = _sealed(_good_entry(permissions={"network_level": "none"}))
    with pytest.raises(PreinstallError):
        _start(monkeypatch, entry)
    assert _FakePopen.instances == []
    assert all("create" not in r for r in runs)              # never auto-created a volume


def test_content_hash_mismatch_refused_no_start(monkeypatch):
    _backend(monkeypatch, hash_hex="b" * 64)                 # verifier returns a different hash
    _patch_inspect(monkeypatch)
    entry = _sealed(_good_entry(permissions={"network_level": "none"}))
    with pytest.raises(PreinstallError):
        _start(monkeypatch, entry)
    assert _FakePopen.instances == []                        # verifier failed before start


def test_backend_unavailable_failclosed(monkeypatch):
    _backend(monkeypatch, available=False)
    _patch_inspect(monkeypatch)
    entry = _sealed(_good_entry(permissions={"network_level": "none"}))
    with pytest.raises(SandboxRequiredError):
        _start(monkeypatch, entry)
    assert _FakePopen.instances == []


def test_env_keys_still_refused_even_if_preinstalled(monkeypatch):
    from agentnode_sdk.runtimes.mcp_consent import CredentialedMcpRefused
    _backend(monkeypatch)
    _patch_inspect(monkeypatch)
    entry = _sealed(_good_entry(permissions={"network_level": "none"}))
    with pytest.raises(CredentialedMcpRefused):
        _start(monkeypatch, entry, env_keys=["OPENAI_API_KEY"])
    assert _FakePopen.instances == []


def test_missing_network_level_is_none_not_default(monkeypatch):
    _backend(monkeypatch)
    _patch_inspect(monkeypatch)
    entry = _sealed(_good_entry())                           # no permissions at all
    _start(monkeypatch, entry)
    margv = _FakePopen.instances[-1].args
    assert "--network" in margv and margv[margv.index("--network") + 1] == "none"


def test_not_preinstalled_refused(monkeypatch):
    """MCP net-isolation (Fallback C): a non-preinstalled MCP (mcp_command-only, no pinned
    mcp_install) is REFUSED — no verifier, no container, never the old open-network npx path."""
    _, rec = _backend(monkeypatch)
    _patch_inspect(monkeypatch)
    entry = _sealed({"version": MCP_VERSION, "runtime": "mcp", "mcp_command": NPX_CMD})
    with pytest.raises(SandboxRequiredError, match="not preinstalled"):
        _start(monkeypatch, entry)
    assert rec.calls == []                                   # no verifier ran
    assert _FakePopen.instances == []                        # nothing launched


def test_mcp_command_never_parsed_for_preinstalled(monkeypatch):
    """mcp_command carries an npx spec but is neither parsed nor used; the launched argv
    comes only from the validated mcp_preinstall_command."""
    _backend(monkeypatch)
    _patch_inspect(monkeypatch)
    entry = _sealed(_good_entry(
        mcp_command=["npx", "-y", "@scope/EVIL@9.9.9"],
        permissions={"network_level": "none"}))
    _start(monkeypatch, entry)
    margv = _FakePopen.instances[-1].args
    assert "EVIL" not in " ".join(margv)
    assert margv[-2:] == ["node", "/install/bin/some-mcp"]


# ===========================================================================
# MCP net-isolation (Fallback C): preinstalled non-credentialed egress allowlist
# ===========================================================================

class _EgressRec:
    """Records egress-proxy start/stop; returns a handle with a REAL EgressSpec so the genuine
    ContainerBackend.wrap_command produces a real ``--network <int_net>`` argv. No secret here."""

    def __init__(self):
        self.started_domains = None
        self.stopped = 0

    def start(self, domains, **kw):
        self.started_domains = list(domains)
        return SimpleNamespace(
            spec=EgressSpec(network_name="agentnode-egress-test-int",
                            proxy_url="http://egress-proxy:8888",
                            allowed_domains=tuple(domains)),
            runtime="docker", int_net="agentnode-egress-test-int", ext_net="x", proxy_name="p")

    def stop(self, handle):
        self.stopped += 1


def _patch_egress(monkeypatch):
    import agentnode_sdk.sandbox.egress as egress
    rec = _EgressRec()
    monkeypatch.setattr(egress, "start_egress_proxy", rec.start)
    monkeypatch.setattr(egress, "stop_egress_proxy", rec.stop)
    return rec


def test_preinstalled_with_allowed_domains_uses_egress(monkeypatch):
    """Preinstalled non-credentialed MCP with a sealed mcp_allowed_domains runs behind the
    REUSED egress allowlist proxy (network=egress on the proxy's internal net), NOT open — and
    with NO secret flow."""
    _backend(monkeypatch)
    _patch_inspect(monkeypatch)
    rec = _patch_egress(monkeypatch)
    entry = _sealed(_good_entry(mcp_allowed_domains=["api.github.com", "API.GitHub.com."]))
    _start(monkeypatch, entry)
    # egress proxy started with the canonicalized (lowercased, de-duped) allowlist
    assert rec.started_domains == ["api.github.com"]
    margv = _FakePopen.instances[-1].args
    # container runs on the egress internal net — not open "default", not plain "none"
    assert margv[margv.index("--network") + 1] == "agentnode-egress-test-int"
    assert margv[-2:] == ["node", "/install/bin/some-mcp"]   # sealed command, not npx


def test_invalid_allowed_domains_is_none_not_open(monkeypatch):
    """Invalid/empty mcp_allowed_domains must NOT fall back to an open network — it becomes
    network=none and no egress proxy is started (fail-safe, never default)."""
    _backend(monkeypatch)
    _patch_inspect(monkeypatch)
    rec = _patch_egress(monkeypatch)
    entry = _sealed(_good_entry(mcp_allowed_domains=["not a domain", "http://x/", "*.evil"]))
    _start(monkeypatch, entry)
    assert rec.started_domains is None                       # egress proxy never started
    margv = _FakePopen.instances[-1].args
    assert margv[margv.index("--network") + 1] == "none"     # isolated, never open


def test_preinstalled_container_env_is_clean(monkeypatch):
    """The preinstalled MCP container gets a clean HOME and no host env/mounts (only the sealed
    RO /install volume). Relocated from test_mcp_sandbox (the old non-preinstalled path is gone)."""
    _backend(monkeypatch)
    _patch_inspect(monkeypatch)
    monkeypatch.setenv("HOME", "/home/realuser")
    entry = _sealed(_good_entry(permissions={"network_level": "none"}))
    _start(monkeypatch, entry)
    margv = _FakePopen.instances[-1].args
    joined = " ".join(margv)
    assert "HOME=/sandbox-home" in margv                     # clean container HOME
    assert "/home/realuser" not in joined                    # no host HOME
    assert ".agentnode" not in joined                        # credential store never mounted
    assert f"{_volume()}:/install:ro" in margv               # only the sealed RO volume


def test_stop_removes_preinstalled_container(monkeypatch):
    """stop() removes the preinstalled MCP container by its own name (lifecycle)."""
    _backend(monkeypatch)
    runs = _patch_inspect(monkeypatch)
    entry = _sealed(_good_entry(permissions={"network_level": "none"}))
    server = _start(monkeypatch, entry)
    name = server._container_name
    assert name and name.startswith("agentnode-mcp-")
    server.stop()
    assert ["docker", "rm", "-f", name] in runs
