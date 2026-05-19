"""Tests for runtime_run dispatch audit (Phase 11.2).

Verifies that runner.py emits runtime_run audit events after
each runtime dispatch (python, mcp, remote, agent).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from agentnode_sdk.models import RunToolResult
from agentnode_sdk.policy import PolicyResult


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    cfg_dir = tmp_path / ".agentnode"
    cfg_dir.mkdir()
    cfg_file = cfg_dir / "config.json"
    cfg_file.write_text(json.dumps({
        "version": "0.1",
        "trust": {"minimum_trust_level": "unverified"},
        "permissions": {
            "network": "allow",
            "filesystem": "allow",
            "code_execution": "allow",
        },
    }))
    lock_file = tmp_path / "agentnode.lock"
    lock_file.write_text(json.dumps({
        "lockfile_version": "0.1",
        "packages": {},
    }))
    monkeypatch.setenv("AGENTNODE_CONFIG", str(cfg_file))
    monkeypatch.setenv("AGENTNODE_CONFIG_DIR", str(cfg_dir))
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(lock_file))
    monkeypatch.delenv("AGENTNODE_GUARD_STRICT", raising=False)
    monkeypatch.delenv("AGENTNODE_NON_INTERACTIVE", raising=False)
    from agentnode_sdk.guard import reset_guard_config_cache, reset_rate_limits
    reset_guard_config_cache()
    reset_rate_limits()
    yield tmp_path


def _read_audit(tmp_path: Path) -> list[dict]:
    audit_path = tmp_path / ".agentnode" / "audit.jsonl"
    if not audit_path.exists():
        return []
    lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines]


def _make_lockfile_entry(
    tmp_path: Path,
    slug: str,
    runtime: str = "python",
    tool_name: str = "get_data",
    **extra,
) -> None:
    lock_file = tmp_path / "agentnode.lock"
    data = json.loads(lock_file.read_text(encoding="utf-8"))
    entry = {
        "version": "1.0.0",
        "package_type": "toolpack",
        "runtime": runtime,
        "entrypoint": "fake_mod:run",
        "trust_level": "verified",
        "installed_at": "2026-05-19T00:00:00Z",
        "permissions": {
            "network_level": "none",
            "filesystem_level": "none",
            "code_execution_level": "none",
        },
        "tools": [{"name": tool_name, "action_type": "read"}],
        **extra,
    }
    data["packages"][slug] = entry
    lock_file.write_text(json.dumps(data), encoding="utf-8")


class TestRuntimeRunAudit:
    """runtime_run audit events for dispatch results."""

    def test_audit_helper_success(self, tmp_path):
        """_audit_runtime_run writes runtime_run on success."""
        from agentnode_sdk.runner import _audit_runtime_run

        res = RunToolResult(success=True, result="ok", mode_used="subprocess")
        entry = {"trust_level": "verified"}
        _audit_runtime_run("test-pack", "my_tool", "python", res, entry)

        entries = _read_audit(tmp_path)
        rt_entries = [e for e in entries if e["event"] == "runtime_run"]
        assert len(rt_entries) == 1
        assert rt_entries[0]["slug"] == "test-pack"
        assert rt_entries[0]["tool_name"] == "my_tool"
        assert rt_entries[0]["action"] == "allow"
        assert rt_entries[0]["source"] == "runner.dispatch"
        assert "runtime=python" in rt_entries[0]["reason"]
        assert "success=True" in rt_entries[0]["reason"]
        assert rt_entries[0]["trust"] == "verified"

    def test_audit_helper_failure(self, tmp_path):
        """_audit_runtime_run writes runtime_run on failure with error summary."""
        from agentnode_sdk.runner import _audit_runtime_run

        res = RunToolResult(
            success=False,
            error="TimeoutError: tool timed out after 30s",
            mode_used="mcp",
        )
        entry = {"trust_level": "unverified"}
        _audit_runtime_run("test-mcp", "search", "mcp", res, entry)

        entries = _read_audit(tmp_path)
        rt_entries = [e for e in entries if e["event"] == "runtime_run"]
        assert len(rt_entries) == 1
        assert rt_entries[0]["action"] == "deny"
        assert "runtime=mcp" in rt_entries[0]["reason"]
        assert "success=False" in rt_entries[0]["reason"]
        assert "TimeoutError" in rt_entries[0]["reason"]

    def test_audit_helper_never_crashes(self, tmp_path):
        """Audit failure must not propagate to caller."""
        from agentnode_sdk.runner import _audit_runtime_run

        res = RunToolResult(success=True, result="ok", mode_used="subprocess")
        with mock.patch(
            "agentnode_sdk.runner.audit_decision",
            side_effect=OSError("disk full"),
        ):
            _audit_runtime_run("test-pack", "my_tool", "python", res, {})

    def test_no_kwargs_in_audit(self, tmp_path):
        """Audit entries must not contain tool arguments or result data."""
        from agentnode_sdk.runner import _audit_runtime_run

        res = RunToolResult(success=True, result={"password": "hunter2"}, mode_used="subprocess")
        _audit_runtime_run("test-pack", "get_data", "python", res, {"trust_level": "verified"})

        entries = _read_audit(tmp_path)
        rt_entries = [e for e in entries if e["event"] == "runtime_run"]
        raw = json.dumps(rt_entries[0])
        assert "hunter2" not in raw
        assert "password" not in raw
        assert "kwargs" not in raw


class TestRuntimeRunIntegration:
    """Verify run_tool() wires _audit_runtime_run at dispatch points."""

    def test_python_dispatch_audits(self, tmp_path):
        """Python runtime dispatch emits runtime_run."""
        _make_lockfile_entry(tmp_path, "test-pack")

        from agentnode_sdk.runner import run_tool

        mock_result = RunToolResult(success=True, result="ok", mode_used="subprocess")
        with mock.patch("agentnode_sdk.runtimes.python_runner.run_python", return_value=mock_result):
            res = run_tool("test-pack", "get_data", lockfile_path=tmp_path / "agentnode.lock")

        assert res.success
        entries = _read_audit(tmp_path)
        rt_entries = [e for e in entries if e["event"] == "runtime_run"]
        assert len(rt_entries) == 1
        assert "runtime=python" in rt_entries[0]["reason"]
        assert rt_entries[0]["action"] == "allow"

    def test_mcp_dispatch_audits(self, tmp_path):
        """MCP runtime dispatch emits runtime_run."""
        _make_lockfile_entry(
            tmp_path, "test-mcp",
            runtime="mcp",
            mcp_command=["python", "-m", "fake"],
        )

        from agentnode_sdk.runner import run_tool

        mock_result = RunToolResult(success=True, result="ok", mode_used="mcp")
        with mock.patch("agentnode_sdk.runtimes.mcp_runner.run_mcp", return_value=mock_result):
            res = run_tool("test-mcp", "get_data", lockfile_path=tmp_path / "agentnode.lock")

        assert res.success
        entries = _read_audit(tmp_path)
        rt_entries = [e for e in entries if e["event"] == "runtime_run"]
        assert len(rt_entries) == 1
        assert "runtime=mcp" in rt_entries[0]["reason"]

    def test_remote_dispatch_audits(self, tmp_path):
        """Remote runtime dispatch emits runtime_run."""
        _make_lockfile_entry(
            tmp_path, "test-remote",
            runtime="remote",
            remote_endpoint="https://api.example.com",
        )

        from agentnode_sdk.runner import run_tool

        mock_result = RunToolResult(success=False, error="HTTP 500", mode_used="remote")
        with mock.patch("agentnode_sdk.runtimes.remote_runner.run_remote", return_value=mock_result):
            res = run_tool("test-remote", "get_data", lockfile_path=tmp_path / "agentnode.lock")

        assert not res.success
        entries = _read_audit(tmp_path)
        rt_entries = [e for e in entries if e["event"] == "runtime_run"]
        assert len(rt_entries) == 1
        assert "runtime=remote" in rt_entries[0]["reason"]
        assert rt_entries[0]["action"] == "deny"
        assert "HTTP 500" in rt_entries[0]["reason"]

    def test_policy_deny_no_runtime_run(self, tmp_path):
        """Policy deny returns before dispatch — no runtime_run audit."""
        lock_file = tmp_path / "agentnode.lock"
        data = json.loads(lock_file.read_text(encoding="utf-8"))
        data["packages"]["blocked-pack"] = {
            "version": "1.0.0",
            "package_type": "toolpack",
            "runtime": "python",
            "entrypoint": "fake_mod:run",
            "trust_level": "unverified",
            "installed_at": "2026-05-19T00:00:00Z",
            "permissions": None,
            "tools": [],
        }
        lock_file.write_text(json.dumps(data), encoding="utf-8")

        cfg_file = tmp_path / ".agentnode" / "config.json"
        cfg_file.write_text(json.dumps({
            "version": "0.1",
            "trust": {"minimum_trust_level": "trusted"},
            "permissions": {},
        }))

        from agentnode_sdk.runner import run_tool
        from agentnode_sdk.guard import reset_guard_config_cache
        reset_guard_config_cache()

        res = run_tool("blocked-pack", lockfile_path=tmp_path / "agentnode.lock")

        assert not res.success
        entries = _read_audit(tmp_path)
        rt_entries = [e for e in entries if e["event"] == "runtime_run"]
        assert len(rt_entries) == 0
