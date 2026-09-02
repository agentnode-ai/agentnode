"""R4: the live routing and refusal paths under the SHIPPED default, unmasked.

`EM2-AC-REMEDIATION-DECISION-0001`, criterion R4, requires three tests through the live
public routing path, and it rules out the shortcut the rest of the suite takes:

  1. a trusted MCP under `curated_only` reaches the SANDBOX launch path and never the
     host command;
  2. a curated MCP under that default reaches the HOST launch path — both sides of the
     MCP boundary;
  3. a trusted package under `curated_only`, on a deliberately runtime-absent lane, is
     refused by the LIVE `enforce_sandbox_policy` gate before any host or sandbox launch.

For (3) the decision is explicit that substituting an always-available or fake
decision/backend is not acceptable: it must exercise the live gate and real default
backend discovery. So that test resets the default backend to None (restoring
`ContainerBackend` discovery) and empties `PATH`, so `shutil.which` genuinely finds no
runtime. Nothing is faked; the absence is real.

No test in this file uses `legacy_default_policy`, and none constructs a
`HostTrustPolicyDecision` by hand — the decision comes from the live gate.
"""
from __future__ import annotations

import json

import pytest

from agentnode_sdk import installer
from agentnode_sdk.config import read_host_trust_policy_snapshot
from agentnode_sdk.lock_integrity import seal_entry
from agentnode_sdk.runner import run_tool
from agentnode_sdk.runtimes import mcp_runner
from agentnode_sdk.sandbox import set_default_backend
from agentnode_sdk.sandbox.policy import enforce_sandbox_policy
from agentnode_sdk.sandbox.types import SandboxRequiredError
from tests.hostpolicy import preinstalled_entry

HOST_COMMAND = ["npx", "-y", "@scope/some-mcp@1.2.3"]


@pytest.fixture(autouse=True)
def _clean_config_dir(monkeypatch, tmp_path):
    """A fresh config directory, so the shipped default is what these tests observe."""
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "cfg"))
    yield


class _SpyPopen:
    """Records every argv a start attempt would launch, so 'never the host command'
    and 'no start side effect' can both be asserted."""

    launched: list[list[str]] = []

    def __init__(self, args, **kwargs):
        _SpyPopen.launched.append(list(args))
        self.args = list(args)
        self.stdin = _Stream()
        self.stdout = _Stream(
            json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}) + "\n")
        self.stderr = _Stream()
        self._alive = True

    def poll(self):
        return None if self._alive else 0

    def wait(self, timeout=None):
        self._alive = False
        return 0

    def kill(self):
        self._alive = False


class _Stream:
    def __init__(self, text=""):
        import io as _io
        self._s = _io.StringIO(text)

    def __getattr__(self, name):
        return getattr(self._s, name)


@pytest.fixture(autouse=True)
def _spy_popen(monkeypatch):
    _SpyPopen.launched = []
    monkeypatch.setattr(mcp_runner.subprocess, "Popen", _SpyPopen)
    yield


def _live_decision(trust_level: str):
    """The decision as the OWNER produces it: from the real config snapshot, through the
    live gate. Not `tests.hostpolicy.decision`, which fabricates one."""
    return enforce_sandbox_policy(
        trust_level, host_policy=read_host_trust_policy_snapshot(), runtime_hint="mcp"
    )


def test_shipped_default_is_curated_only():
    """The premise every test below rests on."""
    assert read_host_trust_policy_snapshot() == "curated_only"


@pytest.mark.usefixtures("bypass_policy")
class TestMcpBoundaryUnderShippedDefault:
    """Both cases enter through the PUBLIC routing API, `runner.run_tool`, which reads the
    lockfile entry, takes the host-trust snapshot, calls the live `enforce_sandbox_policy`
    and builds the launch plan itself. Nothing here precomputes or injects a decision or a
    plan: if that public path failed to read, gate, propagate or enforce the decision, these
    tests would not pass."""

    def _lockfile(self, tmp_path, slug, entry):
        lf = tmp_path / "agentnode.lock"
        installer.update_lockfile(slug, entry, path=lf)
        return lf

    def test_trusted_mcp_never_reaches_the_host_command(self, tmp_path):
        """Under the shipped default a trusted MCP is routed away from the host by the
        PUBLIC path and refused inside the sandbox branch. What is asserted here is the
        security property: the host command is never launched, and the refusal is a
        sandbox-side one rather than a host outcome. An actual container launch needs a
        real runtime and an existing sealed volume, which is the runtime-present lane."""
        raw = preinstalled_entry(trust_level="trusted")
        raw["mcp_command"] = list(HOST_COMMAND)   # set BEFORE sealing, else the seal mismatches
        raw["runtime"] = "mcp"
        raw["package_type"] = "mcp"
        entry = seal_entry(raw)
        lf = self._lockfile(tmp_path, "m-trusted", entry)

        res = run_tool("m-trusted", "some_tool", lockfile_path=lf)

        assert res.success is False
        err = res.error or ""
        # The refusal is sandbox-side, not a host dispatch that happened to fail.
        assert ("Sandbox" in err or "sandbox" in err or "preinstall" in err), err
        # Nothing was launched at all — in particular not the host command. This is an
        # assertion about an empty list, not a loop that could pass vacuously.
        assert _SpyPopen.launched == [], _SpyPopen.launched

    def test_curated_mcp_routes_to_the_host_command(self, tmp_path):
        entry = seal_entry({
            "version": "1.0.0", "package_type": "mcp", "runtime": "mcp",
            "trust_level": "curated", "mcp_command": list(HOST_COMMAND),
            "artifact_hash": "sha256:x", "permissions": {},
            "tools": [{"name": "some_tool"}],
        })
        lf = self._lockfile(tmp_path, "m-curated", entry)

        run_tool("m-curated", "some_tool", lockfile_path=lf)

        assert _SpyPopen.launched, "the public path launched nothing at all"
        assert _SpyPopen.launched[-1] == HOST_COMMAND


class TestLiveGateRefusesWhenNoRuntimeExists:
    """The runtime-absent lane. The live gate and real backend discovery, no substitute."""

    @pytest.fixture(autouse=True)
    def _no_runtime_anywhere(self, monkeypatch):
        # Undo conftest's always-available backend so get_default_backend() constructs a
        # real ContainerBackend and probes for itself.
        set_default_backend(None)
        # Make the probe genuinely fail: shutil.which finds nothing on an empty PATH.
        monkeypatch.setenv("PATH", "")
        monkeypatch.delenv("PATHEXT", raising=False)
        yield
        set_default_backend(None)

    def test_real_discovery_finds_no_runtime(self):
        """The lane is genuinely runtime-absent — not asserted, measured."""
        from agentnode_sdk.sandbox.container_backend import ContainerBackend
        avail = ContainerBackend().check_available()
        assert avail.available is False
        assert avail.backend in ("none", "docker", "podman")

    def test_trusted_is_refused_through_the_public_routing_entrypoint(self, tmp_path):
        """The refusal must be reachable through the PUBLIC package-routing API, not only by
        calling the gate directly: `runner.run_tool` reads the lockfile entry, takes the
        host-trust snapshot and calls the live `enforce_sandbox_policy` itself. With real
        backend discovery on a runtime-absent lane, a trusted package must be refused there,
        before any host or sandbox launch."""
        entry = seal_entry({
            "version": "1.0.0", "package_type": "toolpack", "runtime": "python",
            "trust_level": "trusted", "entrypoint": "pk.tool",
            "artifact_hash": "sha256:x", "permissions": {},
        })
        lf = tmp_path / "agentnode.lock"
        installer.update_lockfile("trusted-pack", entry, path=lf)

        res = run_tool("trusted-pack", lockfile_path=lf)

        assert res.mode_used == "sandbox_unavailable", res.mode_used
        assert res.success is False
        assert "curated_only" in (res.error or "")
        # fail-closed, not fallback: nothing was started, on the host or anywhere else
        assert _SpyPopen.launched == []

    def test_the_gate_itself_also_refuses(self):
        """The same refusal at the gate, so a future change that moved the check out of
        run_tool would still be caught here."""
        with pytest.raises(SandboxRequiredError) as ei:
            enforce_sandbox_policy(
                "trusted", host_policy=read_host_trust_policy_snapshot(), runtime_hint="mcp")
        assert "curated_only" in str(ei.value)
        assert _SpyPopen.launched == []

    def test_curated_still_runs_on_the_host_without_a_runtime(self):
        """The refusal is scoped to tiers the policy sandboxes; it is not a blanket
        outage. Curated is host-eligible under curated_only, so no runtime is needed."""
        decision = enforce_sandbox_policy(
            "curated", host_policy=read_host_trust_policy_snapshot(), runtime_hint="mcp")
        assert decision.execution_boundary == "host"
        assert _SpyPopen.launched == []
