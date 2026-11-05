"""Task #9: a run that did not happen must not look like one that did.

Measured on Windows 11 with no container runtime: ``agentnode run <verified pack> --input '{}'``
printed ``None`` and exited 0, while ``--json`` of the same run said success=false,
mode_used=sandbox_unavailable. Someone on Windows or macOS -- where no runtime is the normal
starting point -- was told nothing. Now a failed run says why on stderr and exits 1, and when
the sandbox is missing it shows the same structured refusal ``agentnode sandbox doctor`` shows
(one classifier), with the steps for this platform and the check to run afterwards.
Synthetic packs and a probe double; nothing is installed or run.
"""
from __future__ import annotations

import sys

import pytest

from agentnode_sdk.models import RunToolResult
from agentnode_sdk.sandbox.types import SandboxAvailability

REFUSED = RunToolResult(
    success=False, mode_used="sandbox_unavailable",
    error="Community package execution requires a container runtime (Docker or Podman). "
          "no container runtime (docker or podman) found on PATH")


class _NoRuntime:
    def check_available(self):
        return SandboxAvailability(available=False, backend="none",
                                   reason="no container runtime (docker or podman) found on PATH")


@pytest.fixture
def run_returns(monkeypatch):
    from agentnode_sdk.sandbox import policy

    monkeypatch.setenv("AGENTNODE_NON_INTERACTIVE", "1")
    monkeypatch.setattr(policy, "get_default_backend", lambda: _NoRuntime())

    def set_result(result):
        monkeypatch.setattr("agentnode_sdk.runner.run_tool", lambda *a, **k: result)
    return set_result


def _run(capsys, **kw):
    from agentnode_sdk.cli.commands import cmd_run

    rc = cmd_run("some-pack", input_data="{}", **kw)
    out = capsys.readouterr()
    return rc, out.out, out.err


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_a_missing_sandbox_is_explained_for_this_platform(run_returns, capsys, monkeypatch, platform):
    monkeypatch.setattr(sys, "platform", platform)
    run_returns(REFUSED)
    rc, out, err = _run(capsys)
    assert rc == 1
    assert "None" not in out
    assert "Docker Desktop" in err                     # the way out on Windows and macOS
    assert "agentnode sandbox doctor" in err           # and how to check it worked
    assert "did not run" in err                        # nothing happened, nothing to undo
    assert "sudo" not in err and "apt" not in err      # no Linux commands on these platforms


def test_any_failed_run_says_why_and_exits_nonzero(run_returns, capsys):
    run_returns(RunToolResult(success=False, mode_used="direct", error="synthetic tool failure"))
    rc, out, err = _run(capsys)
    assert rc == 1
    assert "synthetic tool failure" in err
    assert "None" not in out


def test_a_failed_raw_run_exits_nonzero_too(run_returns, capsys):
    run_returns(RunToolResult(success=False, mode_used="direct", error="synthetic tool failure"))
    rc, out, err = _run(capsys, raw=True)
    assert rc == 1 and "synthetic tool failure" in err


def test_a_successful_run_is_unchanged(run_returns, capsys):
    run_returns(RunToolResult(success=True, result={"answer": 42}))
    rc, out, err = _run(capsys)
    assert rc == 0 and "42" in out and err == ""


def test_json_output_is_unchanged(run_returns, capsys):
    import json

    run_returns(REFUSED)
    rc, out, err = _run(capsys, json_output=True)
    assert rc == 1
    assert json.loads(out)["mode_used"] == "sandbox_unavailable" and err == ""
