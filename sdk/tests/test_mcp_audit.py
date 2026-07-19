"""Tests for MCP runtime audit parity (Phase 11.1).

Verifies that mcp_runner emits mcp_run audit events for actual
tool execution results, analogous to remote_runner's remote_run events.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from agentnode_sdk.policy import PolicyResult, audit_decision


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Minimal environment isolation for audit tests."""
    cfg_dir = tmp_path / ".agentnode"
    cfg_dir.mkdir()
    cfg_file = cfg_dir / "config.json"
    cfg_file.write_text(json.dumps({"version": "0.1"}))
    monkeypatch.setenv("AGENTNODE_CONFIG", str(cfg_file))
    monkeypatch.setenv("AGENTNODE_CONFIG_DIR", str(cfg_dir))
    lock_file = tmp_path / "agentnode.lock"
    lock_file.write_text(json.dumps({
        "lockfile_version": "0.1",
        "packages": {},
    }))
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(lock_file))
    # Reset guard config cache between tests
    from agentnode_sdk.guard import reset_guard_config_cache
    reset_guard_config_cache()
    yield tmp_path


def _read_audit(tmp_path: Path) -> list[dict]:
    audit_path = tmp_path / ".agentnode" / "audit.jsonl"
    if not audit_path.exists():
        return []
    lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines]


class TestMcpRunAudit:
    """mcp_run audit events for execution results."""

    def test_success_audit(self, tmp_path):
        """Successful MCP tool call writes an mcp_run audit entry."""
        from agentnode_sdk.runtimes.mcp_runner import _audit_mcp_call

        _audit_mcp_call("test-mcp-pack", "search", success=True, duration_ms=42.7)

        entries = _read_audit(tmp_path)
        mcp_entries = [e for e in entries if e["event"] == "mcp_run"]
        assert len(mcp_entries) == 1

        entry = mcp_entries[0]
        assert entry["slug"] == "test-mcp-pack"
        assert entry["tool_name"] == "search"
        assert entry["action"] == "allow"
        assert entry["source"] == "mcp_runner"
        assert "duration_ms=43" in entry["reason"]
        assert "ok" in entry["reason"]

    def test_failure_audit(self, tmp_path):
        """Failed MCP tool call writes an mcp_run audit entry with error class."""
        from agentnode_sdk.runtimes.mcp_runner import _audit_mcp_call

        _audit_mcp_call(
            "test-mcp-pack", "search",
            success=False, duration_ms=30000.0, error_class="TimeoutError",
        )

        entries = _read_audit(tmp_path)
        mcp_entries = [e for e in entries if e["event"] == "mcp_run"]
        assert len(mcp_entries) == 1

        entry = mcp_entries[0]
        assert entry["action"] == "deny"
        assert entry["source"] == "mcp_runner"
        assert "failed" in entry["reason"]
        assert "TimeoutError" in entry["reason"]
        assert "duration_ms=30000" in entry["reason"]

    def test_failure_without_error_class(self, tmp_path):
        """Failed MCP call without error class still writes reason."""
        from agentnode_sdk.runtimes.mcp_runner import _audit_mcp_call

        _audit_mcp_call("test-mcp-pack", "query", success=False, duration_ms=100.0)

        entries = _read_audit(tmp_path)
        mcp_entries = [e for e in entries if e["event"] == "mcp_run"]
        assert len(mcp_entries) == 1

        entry = mcp_entries[0]
        assert entry["action"] == "deny"
        assert "failed" in entry["reason"]
        assert "duration_ms=100" in entry["reason"]
        # No error class in reason
        assert "error=" not in entry["reason"]

    def test_no_args_in_audit(self, tmp_path):
        """Audit entries must not contain tool arguments or secrets."""
        from agentnode_sdk.runtimes.mcp_runner import _audit_mcp_call

        _audit_mcp_call("test-mcp-pack", "search", success=True, duration_ms=50.0)

        entries = _read_audit(tmp_path)
        mcp_entries = [e for e in entries if e["event"] == "mcp_run"]
        assert len(mcp_entries) == 1

        raw = json.dumps(mcp_entries[0])
        # No kwargs, response payloads, or schema data
        assert "kwargs" not in raw
        assert "arguments" not in raw
        assert "result" not in raw.lower().replace("policyresult", "")
        assert "schema" not in raw

    def test_audit_never_crashes(self, tmp_path):
        """Audit failure must not propagate to caller."""
        from agentnode_sdk.runtimes.mcp_runner import _audit_mcp_call

        with mock.patch(
            "agentnode_sdk.runtimes.mcp_runner.audit_decision",
            side_effect=OSError("disk full"),
        ):
            # Must not raise
            _audit_mcp_call("test-mcp-pack", "search", success=True, duration_ms=10.0)

    def test_tool_name_none(self, tmp_path):
        """Audit works when tool_name is None (single-tool packs)."""
        from agentnode_sdk.runtimes.mcp_runner import _audit_mcp_call

        _audit_mcp_call("test-mcp-pack", None, success=True, duration_ms=20.0)

        entries = _read_audit(tmp_path)
        mcp_entries = [e for e in entries if e["event"] == "mcp_run"]
        assert len(mcp_entries) == 1
        assert mcp_entries[0]["tool_name"] is None


class TestMcpRunnerAuditIntegration:
    """Verify run_mcp() wires audit calls at the right return paths."""

    def test_success_path_audits(self, tmp_path):
        """Successful run_mcp() emits mcp_run audit."""
        from tests.hostpolicy import run_mcp

        mock_server = mock.MagicMock()
        mock_server.call_tool.return_value = {"content": [{"text": "ok"}]}
        mock_server.health_check.return_value = True

        entry = {
            "mcp_command": ["python", "-m", "fake_server"],
            "tools": [{"name": "search"}],
            "permissions": {},
        }

        with mock.patch("agentnode_sdk.runtimes.mcp_runner._get_global_pool") as mock_pool, \
             mock.patch("agentnode_sdk.guard.inspect_mcp_args") as mock_inspect:
            mock_pool.return_value.get_or_start.return_value = mock_server
            from agentnode_sdk.guard import GuardDecision
            mock_inspect.return_value = GuardDecision(
                action="allow", reason="ok", source="guard.mcp_inspection",
            )
            result = run_mcp("test-mcp-pack", "search", timeout=5.0, entry=entry)

        assert result.success
        entries = _read_audit(tmp_path)
        mcp_entries = [e for e in entries if e["event"] == "mcp_run"]
        assert len(mcp_entries) == 1
        assert mcp_entries[0]["action"] == "allow"
        assert mcp_entries[0]["slug"] == "test-mcp-pack"
        assert mcp_entries[0]["tool_name"] == "search"

    def test_timeout_path_audits(self, tmp_path):
        """TimeoutError in run_mcp() emits mcp_run audit with error class."""
        from tests.hostpolicy import run_mcp

        mock_server = mock.MagicMock()
        mock_server.call_tool.side_effect = TimeoutError("read timeout")
        mock_server.health_check.return_value = True

        entry = {
            "mcp_command": ["python", "-m", "fake_server"],
            "tools": [{"name": "search"}],
            "permissions": {},
        }

        with mock.patch("agentnode_sdk.runtimes.mcp_runner._get_global_pool") as mock_pool, \
             mock.patch("agentnode_sdk.guard.inspect_mcp_args") as mock_inspect:
            mock_pool.return_value.get_or_start.return_value = mock_server
            from agentnode_sdk.guard import GuardDecision
            mock_inspect.return_value = GuardDecision(
                action="allow", reason="ok", source="guard.mcp_inspection",
            )
            result = run_mcp("test-mcp-pack", "search", timeout=5.0, entry=entry)

        assert not result.success
        assert result.timed_out
        entries = _read_audit(tmp_path)
        mcp_entries = [e for e in entries if e["event"] == "mcp_run"]
        assert len(mcp_entries) == 1
        assert mcp_entries[0]["action"] == "deny"
        assert "TimeoutError" in mcp_entries[0]["reason"]

    def test_exception_path_audits(self, tmp_path):
        """Generic exception in run_mcp() emits mcp_run audit."""
        from tests.hostpolicy import run_mcp

        entry = {
            "mcp_command": ["python", "-m", "fake_server"],
            "tools": [{"name": "search"}],
            "permissions": {},
        }

        with mock.patch("agentnode_sdk.runtimes.mcp_runner._get_global_pool") as mock_pool, \
             mock.patch("agentnode_sdk.guard.inspect_mcp_args") as mock_inspect:
            mock_pool.return_value.get_or_start.side_effect = RuntimeError("server crashed")
            from agentnode_sdk.guard import GuardDecision
            mock_inspect.return_value = GuardDecision(
                action="allow", reason="ok", source="guard.mcp_inspection",
            )
            result = run_mcp("test-mcp-pack", "search", timeout=5.0, entry=entry)

        assert not result.success
        entries = _read_audit(tmp_path)
        mcp_entries = [e for e in entries if e["event"] == "mcp_run"]
        assert len(mcp_entries) == 1
        assert mcp_entries[0]["action"] == "deny"
        assert "RuntimeError" in mcp_entries[0]["reason"]

    def test_guard_deny_no_mcp_run_audit(self, tmp_path):
        """Guard deny does NOT write mcp_run — it's already audited as guard_check."""
        from tests.hostpolicy import run_mcp

        entry = {
            "mcp_command": ["python", "-m", "fake_server"],
            "tools": [{"name": "search"}],
            "permissions": {},
        }

        mock_server = mock.MagicMock()
        mock_server.health_check.return_value = True

        with mock.patch("agentnode_sdk.runtimes.mcp_runner._get_global_pool") as mock_pool, \
             mock.patch("agentnode_sdk.guard.inspect_mcp_args") as mock_inspect:
            mock_pool.return_value.get_or_start.return_value = mock_server
            from agentnode_sdk.guard import GuardDecision
            mock_inspect.return_value = GuardDecision(
                action="deny",
                reason="Path traversal at query: contains '../'",
                source="guard.mcp_inspection",
            )
            result = run_mcp("test-mcp-pack", "search", timeout=5.0, entry=entry)

        assert not result.success
        entries = _read_audit(tmp_path)
        mcp_entries = [e for e in entries if e["event"] == "mcp_run"]
        assert len(mcp_entries) == 0

    def test_no_command_no_mcp_run_audit(self, tmp_path):
        """Missing mcp_command is a config error, not an execution — no mcp_run audit."""
        from tests.hostpolicy import run_mcp

        entry = {"tools": [{"name": "search"}], "permissions": {}}
        result = run_mcp("test-mcp-pack", "search", timeout=5.0, entry=entry)

        assert not result.success
        entries = _read_audit(tmp_path)
        mcp_entries = [e for e in entries if e["event"] == "mcp_run"]
        assert len(mcp_entries) == 0


class TestMcpEnvKeys:
    """Tests for MCP env_keys handling."""

    def test_missing_env_keys_returns_clear_error(self, tmp_path, monkeypatch):
        """Missing declared env vars produce a clear error naming the keys."""
        from tests.hostpolicy import run_mcp

        monkeypatch.delenv("BRAVE_API_KEY", raising=False)

        entry = {
            "mcp_command": ["npx", "-y", "fake-server"],
            "mcp_env_keys": ["BRAVE_API_KEY"],
            "tools": [{"name": "search"}],
            "permissions": {},
        }
        result = run_mcp("test-mcp-pack", "search", timeout=5.0, entry=entry)

        assert not result.success
        assert "BRAVE_API_KEY" in result.error
        assert "Missing environment variables" in result.error

    def test_multiple_missing_env_keys(self, tmp_path, monkeypatch):
        """Multiple missing env vars are all listed."""
        from tests.hostpolicy import run_mcp

        monkeypatch.delenv("KEY_A", raising=False)
        monkeypatch.delenv("KEY_B", raising=False)

        entry = {
            "mcp_command": ["npx", "-y", "fake-server"],
            "mcp_env_keys": ["KEY_A", "KEY_B"],
            "tools": [{"name": "query"}],
            "permissions": {},
        }
        result = run_mcp("test-mcp-pack", "query", timeout=5.0, entry=entry)

        assert not result.success
        assert "KEY_A" in result.error
        assert "KEY_B" in result.error

    def test_env_keys_set_passes_through(self, tmp_path, monkeypatch):
        """When all env_keys are set, run_mcp proceeds (mocks the server)."""
        from tests.hostpolicy import run_mcp

        monkeypatch.setenv("BRAVE_API_KEY", "test-key-123")

        mock_server = mock.MagicMock()
        mock_server.call_tool.return_value = {"content": [{"text": "ok"}]}
        mock_server.health_check.return_value = True

        entry = {
            "mcp_command": ["npx", "-y", "fake-server"],
            "mcp_env_keys": ["BRAVE_API_KEY"],
            "tools": [{"name": "search"}],
            "permissions": {},
        }

        with mock.patch("agentnode_sdk.runtimes.mcp_runner._get_global_pool") as mock_pool, \
             mock.patch("agentnode_sdk.guard.inspect_mcp_args") as mock_inspect:
            mock_pool.return_value.get_or_start.return_value = mock_server
            from agentnode_sdk.guard import GuardDecision
            mock_inspect.return_value = GuardDecision(
                action="allow", reason="ok", source="guard.mcp_inspection",
            )
            result = run_mcp("test-mcp-pack", "search", timeout=5.0, entry=entry)

        assert result.success

    def test_empty_env_keys_no_error(self, tmp_path):
        """Empty env_keys list does not block execution."""
        from tests.hostpolicy import run_mcp

        mock_server = mock.MagicMock()
        mock_server.call_tool.return_value = {"content": [{"text": "ok"}]}
        mock_server.health_check.return_value = True

        entry = {
            "mcp_command": ["npx", "-y", "fake-server"],
            "mcp_env_keys": [],
            "tools": [{"name": "search"}],
            "permissions": {},
        }

        with mock.patch("agentnode_sdk.runtimes.mcp_runner._get_global_pool") as mock_pool, \
             mock.patch("agentnode_sdk.guard.inspect_mcp_args") as mock_inspect:
            mock_pool.return_value.get_or_start.return_value = mock_server
            from agentnode_sdk.guard import GuardDecision
            mock_inspect.return_value = GuardDecision(
                action="allow", reason="ok", source="guard.mcp_inspection",
            )
            result = run_mcp("test-mcp-pack", "search", timeout=5.0, entry=entry)

        assert result.success

    def test_mcp_env_only_passes_declared_keys(self):
        """_mcp_env only passes env_keys that are declared AND set."""
        from agentnode_sdk.runtimes.mcp_runner import _mcp_env
        import os

        old_val = os.environ.get("BRAVE_API_KEY")
        try:
            os.environ["BRAVE_API_KEY"] = "secret-val"
            env = _mcp_env(env_keys=["BRAVE_API_KEY"])
            assert env["BRAVE_API_KEY"] == "secret-val"

            env_no_keys = _mcp_env(env_keys=[])
            assert "BRAVE_API_KEY" not in env_no_keys

            env_none = _mcp_env()
            assert "BRAVE_API_KEY" not in env_none
        finally:
            if old_val is None:
                os.environ.pop("BRAVE_API_KEY", None)
            else:
                os.environ["BRAVE_API_KEY"] = old_val

    def test_mcp_env_unset_declared_key_not_included(self):
        """A declared but unset env key is simply not passed to subprocess."""
        from agentnode_sdk.runtimes.mcp_runner import _mcp_env
        import os

        os.environ.pop("NONEXISTENT_MCP_KEY_12345", None)
        env = _mcp_env(env_keys=["NONEXISTENT_MCP_KEY_12345"])
        assert "NONEXISTENT_MCP_KEY_12345" not in env
