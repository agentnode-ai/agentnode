"""Slice A — agent sandbox hardening locks (unit-level, no container needed).

These regression-lock the container isolation that already applies to agent
sessions via wrap_command, plus the credential-scoping invariant and the
in-container process-spawn guard. They assert the ARGV and SPEC the way the
gated E2E test (test_agent_session_container.py::test_container_isolation_real)
asserts the real runtime behavior.

Security invariants locked here:
  - Agent containers get CPU / memory / PID limits, read-only rootfs, all caps
    dropped, no-new-privileges, and network=none — none can be silently dropped.
  - The agent container env carries ONLY PYTHONPATH: no host secret ever reaches
    the container via env or argv (the LLM key stays host-side, behind the broker).
  - The in-container wrapper neutralizes fork/exec/subprocess before agent code
    runs (defense-in-depth; the container flags are the real boundary).
"""

from __future__ import annotations

import pytest

from agentnode_sdk.sandbox.container_backend import ContainerBackend
from agentnode_sdk.sandbox.types import MountSpec, ProcessSpec


def _agent_spec() -> ProcessSpec:
    """The exact spec shape run_agent_sandboxed builds for an agent session."""
    from agentnode_sdk.sandbox.agent_container_wrapper import WRAPPER_SOURCE

    return ProcessSpec(
        command=["python", "-c", WRAPPER_SOURCE],
        network="none",
        env={"PYTHONPATH": "/pack"},
        mounts=[MountSpec(src="agentnode-agent-vol", dst="/pack", read_only=True)],
        clean_home=True,
        interactive=True,
        name="agentnode-agent-test",
    )


# ---------------------------------------------------------------------------
# Resource limits + isolation flags (regression lock)
# ---------------------------------------------------------------------------


def test_agent_container_has_resource_limits():
    argv = ContainerBackend(runtime="docker").wrap_command(_agent_spec())
    # CPU / memory / PIDs — the limits the hardening bow requires for agents.
    assert "--memory" in argv and "512m" in argv
    assert "--cpus" in argv and "1" in argv
    assert "--pids-limit" in argv and "256" in argv


def test_agent_container_isolation_flags():
    argv = ContainerBackend(runtime="docker").wrap_command(_agent_spec())
    assert "--read-only" in argv
    assert "--cap-drop=ALL" in argv
    assert "--security-opt=no-new-privileges" in argv
    assert "--user" in argv and "1000:1000" in argv
    # network is explicitly none (no host/internet route from agent code)
    i = argv.index("--network")
    assert argv[i + 1] == "none"
    # /tmp is writable but noexec+nosuid (no dropping and running a binary)
    assert any(a.startswith("/tmp:") and "noexec" in a and "nosuid" in a for a in argv)


def test_agent_container_tmpfs_and_clean_home():
    argv = ContainerBackend(runtime="docker").wrap_command(_agent_spec())
    # a fresh ephemeral HOME, never the host home
    assert "HOME=/sandbox-home" in argv
    assert any(a.startswith("/sandbox-home:") for a in argv)


# ---------------------------------------------------------------------------
# Credential scoping — no host secret crosses the container boundary
# ---------------------------------------------------------------------------


def test_agent_container_env_is_pythonpath_only():
    """The agent container env must carry ONLY PYTHONPATH — never a host secret."""
    spec = _agent_spec()
    assert spec.env == {"PYTHONPATH": "/pack"}
    # no name-passthrough of secrets either (that mechanism is egress-only anyway)
    assert spec.env_passthrough == []


def test_no_secret_in_agent_argv(monkeypatch):
    """Even with secrets in the host env, none appear in the container argv."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-super-secret-value")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret-value")
    argv = ContainerBackend(runtime="docker").wrap_command(_agent_spec())
    joined = " ".join(argv)
    assert "sk-super-secret-value" not in joined
    assert "sk-ant-secret-value" not in joined
    # the only -e values are HOME and PYTHONPATH
    e_values = [argv[i + 1] for i, a in enumerate(argv) if a == "-e"]
    for v in e_values:
        assert v.startswith("HOME=") or v.startswith("PYTHONPATH=")


# ---------------------------------------------------------------------------
# In-container process-spawn guard (defense-in-depth)
# ---------------------------------------------------------------------------


def _run_guard_then(snippet: str):
    """Execute just the guard block from WRAPPER_SOURCE, then eval a probe.

    The guard mutates the shared ``os`` and ``subprocess`` module objects — which
    is exactly right INSIDE the container (a fresh throwaway process), but here it
    runs in the pytest process, so we snapshot and restore those attributes to
    keep the mutation from leaking into other tests.
    """
    import os
    import subprocess

    from agentnode_sdk.sandbox.agent_container_wrapper import WRAPPER_SOURCE

    marker = "_install_process_guard()\n"
    guard_src = WRAPPER_SOURCE[: WRAPPER_SOURCE.index(marker) + len(marker)]

    os_attrs = (
        "fork", "forkpty", "system", "posix_spawn", "posix_spawnp",
        "exec", "execl", "execle", "execlp", "execlpe", "execv", "execve",
        "execvp", "execvpe", "spawnl", "spawnle", "spawnlp", "spawnlpe",
        "spawnv", "spawnve", "spawnvp", "spawnvpe",
    )
    sp_attrs = ("Popen", "run", "call", "check_call", "check_output",
                "getoutput", "getstatusoutput")
    saved_os = {a: getattr(os, a) for a in os_attrs if hasattr(os, a)}
    saved_sp = {a: getattr(subprocess, a) for a in sp_attrs if hasattr(subprocess, a)}
    try:
        ns: dict = {}
        exec(guard_src, ns)  # noqa: S102 — trusted first-party wrapper source
        return eval(snippet, ns)  # noqa: S307 — probe expression under test
    finally:
        for a, v in saved_os.items():
            setattr(os, a, v)
        for a, v in saved_sp.items():
            setattr(subprocess, a, v)


def test_guard_blocks_subprocess_popen():
    with pytest.raises(PermissionError):
        _run_guard_then("__import__('subprocess').Popen(['echo', 'hi'])")


def test_guard_blocks_subprocess_run():
    with pytest.raises(PermissionError):
        _run_guard_then("__import__('subprocess').run(['echo', 'hi'])")


def test_guard_blocks_os_system():
    with pytest.raises(PermissionError):
        _run_guard_then("__import__('os').system('echo hi')")


def test_guard_blocks_os_fork():
    # os.fork exists only on POSIX; on Windows the guard simply has nothing to
    # replace, so skip rather than assert a raise that can't happen.
    import os

    if not hasattr(os, "fork"):
        pytest.skip("os.fork not present on this platform")
    with pytest.raises(PermissionError):
        _run_guard_then("__import__('os').fork()")


def test_guard_leaves_threads_working():
    """The guard must NOT break threads (CPython threads don't os.fork)."""
    result = _run_guard_then(
        "(lambda: (__import__('threading').Thread(target=lambda: None).start() or 'ok'))()"
    )
    assert result == "ok"
