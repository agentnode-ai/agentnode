"""P0.1 sandbox unit tests: detection, the fail-closed tier gate, command wrapping.

No real containers are run. Detection is exercised by mocking shutil.which /
subprocess.run; the gate and wrap_command are pure.
"""
import logging

import pytest

import agentnode_sdk.sandbox.container_backend as cb
from agentnode_sdk.sandbox import (
    ContainerBackend,
    NoSandboxBackend,
    SandboxRequiredError,
    require_sandbox_for_tier,
)
from agentnode_sdk.sandbox.types import ProcessSpec, SandboxAvailability


def _avail(ok: bool) -> SandboxAvailability:
    return SandboxAvailability(
        available=ok, backend="docker" if ok else "none",
        reason="" if ok else "no container runtime found",
    )


# --- detection ----------------------------------------------------------------

def test_detect_no_runtime(monkeypatch):
    monkeypatch.setattr(cb.shutil, "which", lambda name: None)
    a = ContainerBackend().check_available()
    assert a.available is False
    assert "container runtime" in a.reason.lower()


def test_detect_available_and_cached(monkeypatch):
    monkeypatch.setattr(cb.shutil, "which",
                        lambda name: "/usr/bin/docker" if name == "docker" else None)
    calls = []

    def fake_run(args, **k):
        calls.append(args)
        class R:
            returncode = 0
        return R()
    monkeypatch.setattr(cb.subprocess, "run", fake_run)

    be = ContainerBackend()
    a = be.check_available()
    assert a.available is True and a.backend == "docker" and a.daemon_ok
    n = len(calls)
    be.check_available()  # cached — no re-probe
    assert len(calls) == n


def test_detect_daemon_down(monkeypatch):
    monkeypatch.setattr(cb.shutil, "which",
                        lambda name: "/usr/bin/docker" if name == "docker" else None)

    def fake_run(args, **k):
        class R:
            returncode = 1
        return R()
    monkeypatch.setattr(cb.subprocess, "run", fake_run)

    a = ContainerBackend().check_available()
    assert a.available is False
    assert "daemon" in a.reason.lower()


# --- fail-closed tier gate ----------------------------------------------------

def test_gate_verified_blocked():
    with pytest.raises(SandboxRequiredError, match="container runtime"):
        require_sandbox_for_tier("verified", _avail(False))


@pytest.mark.parametrize("tier", ["unverified", None, "weird"])
def test_gate_other_tiers_blocked(tier):
    with pytest.raises(SandboxRequiredError):
        require_sandbox_for_tier(tier, _avail(False))


def test_gate_curated_allowed_without_sandbox():
    require_sandbox_for_tier("curated", _avail(False))  # no raise


def test_gate_trusted_allowed_but_warns(caplog):
    with caplog.at_level(logging.WARNING, logger="agentnode.sandbox"):
        require_sandbox_for_tier("trusted", _avail(False))
    assert any("transition" in r.message.lower() for r in caplog.records)


def test_gate_verified_allowed_when_available():
    require_sandbox_for_tier("verified", _avail(True))  # no raise


# --- command wrapping (pure argv, no execution) -------------------------------

def test_wrap_command_is_hardened_and_never_mounts_host_home():
    be = ContainerBackend(runtime="docker")
    spec = ProcessSpec(command=["python", "-c", "print(1)"], network="none")
    argv = be.wrap_command(spec)

    assert argv[:2] == ["docker", "run"]
    for flag in ("--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges"):
        assert flag in argv
    assert "--network" in argv and "none" in argv
    assert "HOME=/sandbox-home" in argv

    joined = " ".join(argv)
    assert ".agentnode" not in joined
    assert ".ssh" not in joined
    assert "/var/run/docker.sock" not in joined
    assert "--privileged" not in argv

    assert argv[-3:] == ["python", "-c", "print(1)"]  # command last


def test_nosandbox_backend_refuses_to_wrap():
    with pytest.raises(SandboxRequiredError):
        NoSandboxBackend().wrap_command(ProcessSpec(command=["x"]))


def test_run_mcp_process_not_implemented():
    # run_process is implemented in ContainerBackend as of P0.3 (toolpack run);
    # the long-lived MCP path stays on mcp_runner's own launcher (P0.2), so
    # run_mcp_process remains NotImplemented here.
    be = ContainerBackend(runtime="docker")
    with pytest.raises(NotImplementedError):
        be.run_mcp_process(ProcessSpec(command=["x"]))


def test_base_run_process_not_implemented():
    # The abstract base still fails closed: a backend without a real run_process
    # must never silently execute anything.
    from agentnode_sdk.sandbox.backend import SandboxBackend

    class _Bare(SandboxBackend):
        def check_available(self):  # pragma: no cover - trivial
            raise NotImplementedError
        def wrap_command(self, spec):  # pragma: no cover - trivial
            raise NotImplementedError

    with pytest.raises(NotImplementedError):
        _Bare().run_process(ProcessSpec(command=["x"]))
