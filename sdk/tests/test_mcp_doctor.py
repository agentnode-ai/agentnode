"""Tests for agentnode mcp doctor command."""
import json
import os
from unittest import mock

import pytest

from agentnode_sdk.cli.mcp_commands import cmd_mcp_doctor


@pytest.fixture
def mcp_lockfile(tmp_path, monkeypatch):
    """Create a lockfile with an MCP package."""
    lock_path = tmp_path / "agentnode.lock"
    lock_data = {
        "version": "1",
        "packages": {
            "mcp-test": {
                "version": "0.1.0",
                "runtime": "mcp",
                "mcp_command": ["npx", "-y", "test-mcp-server@1.0.0"],
                "mcp_env_keys": ["TEST_API_KEY"],
                "tools": [{"name": "test_tool"}],
                "permissions": {},
            },
            "python-pack": {
                "version": "1.0.0",
                "runtime": "python",
                "entrypoint": "tool.py",
                "tools": [],
                "permissions": {},
            },
        },
    }
    lock_path.write_text(json.dumps(lock_data), encoding="utf-8")
    monkeypatch.setattr("agentnode_sdk.cli.mcp_commands.read_lockfile", lambda: lock_data)
    return lock_data


def test_not_installed(monkeypatch, capsys):
    """Package not in lockfile → exit 1, shows install command."""
    monkeypatch.setattr("agentnode_sdk.cli.mcp_commands.read_lockfile",
                        lambda: {"packages": {}})
    result = cmd_mcp_doctor("nonexistent", json_output=False, skip_start=True)
    assert result == 1
    out = capsys.readouterr().out
    assert "not found" in out.lower() or "not installed" in out.lower()
    assert "agentnode install nonexistent" in out


def test_not_mcp(mcp_lockfile, capsys):
    """Non-MCP package → exit 1, shows runtime mismatch."""
    result = cmd_mcp_doctor("python-pack", json_output=False, skip_start=True)
    assert result == 1
    out = capsys.readouterr().out
    assert "python" in out.lower()
    assert "not an MCP package" in out or "not 'mcp'" in out


def test_node_missing(mcp_lockfile, monkeypatch, capsys):
    """Node.js not found → exit 1, shows install link."""
    monkeypatch.setattr("agentnode_sdk.cli.mcp_commands.shutil.which",
                        lambda x: None)
    result = cmd_mcp_doctor("mcp-test", json_output=False, skip_start=True)
    assert result == 1
    out = capsys.readouterr().out
    assert "node" in out.lower()
    assert "nodejs.org" in out


def test_npx_missing(mcp_lockfile, monkeypatch, capsys):
    """npx not found → exit 1."""
    def fake_which(name):
        if name == "node":
            return "/usr/bin/node"
        return None
    monkeypatch.setattr("agentnode_sdk.cli.mcp_commands.shutil.which", fake_which)
    monkeypatch.setattr("agentnode_sdk.cli.mcp_commands.subprocess.run",
                        lambda *a, **kw: mock.Mock(stdout="v20.0.0\n"))
    result = cmd_mcp_doctor("mcp-test", json_output=False, skip_start=True)
    assert result == 1
    out = capsys.readouterr().out
    assert "npx" in out.lower()


def test_env_var_missing(mcp_lockfile, monkeypatch, capsys):
    """Missing env var → exit 1, names the key."""
    def fake_which(name):
        return f"/usr/bin/{name}"
    monkeypatch.setattr("agentnode_sdk.cli.mcp_commands.shutil.which", fake_which)
    monkeypatch.setattr("agentnode_sdk.cli.mcp_commands.subprocess.run",
                        lambda *a, **kw: mock.Mock(stdout="v20.0.0\n"))
    monkeypatch.delenv("TEST_API_KEY", raising=False)

    result = cmd_mcp_doctor("mcp-test", json_output=False, skip_start=True)
    assert result == 1
    out = capsys.readouterr().out
    assert "TEST_API_KEY" in out
    assert "not set" in out


def test_all_pass_skip_start(mcp_lockfile, monkeypatch, capsys):
    """All checks pass (skip start) → exit 0, shows 'Ready to run'."""
    def fake_which(name):
        return f"/usr/bin/{name}"
    monkeypatch.setattr("agentnode_sdk.cli.mcp_commands.shutil.which", fake_which)
    monkeypatch.setattr("agentnode_sdk.cli.mcp_commands.subprocess.run",
                        lambda *a, **kw: mock.Mock(stdout="v20.0.0\n"))
    monkeypatch.setenv("TEST_API_KEY", "test-key-123")

    result = cmd_mcp_doctor("mcp-test", json_output=False, skip_start=True)
    assert result == 0
    out = capsys.readouterr().out
    assert "Ready to run" in out
    assert "agentnode run mcp-test" in out


def test_all_pass_with_start(mcp_lockfile, monkeypatch, capsys):
    """All checks pass including handshake → exit 0."""
    def fake_which(name):
        return f"/usr/bin/{name}"
    monkeypatch.setattr("agentnode_sdk.cli.mcp_commands.shutil.which", fake_which)
    monkeypatch.setattr("agentnode_sdk.cli.mcp_commands.subprocess.run",
                        lambda *a, **kw: mock.Mock(stdout="v20.0.0\n"))
    monkeypatch.setenv("TEST_API_KEY", "test-key-123")

    mock_server = mock.MagicMock()
    mock_server.start.return_value = None
    mock_server.stop.return_value = None

    with mock.patch("agentnode_sdk.runtimes.mcp_runner.MCPServerProcess",
                    return_value=mock_server) as MockCls:
        result = cmd_mcp_doctor("mcp-test", json_output=False, skip_start=False)

    assert result == 0
    out = capsys.readouterr().out
    assert "Ready to run" in out
    MockCls.assert_called_once_with("mcp-test", ["npx", "-y", "test-mcp-server@1.0.0"])
    mock_server.start.assert_called_once()
    mock_server.stop.assert_called_once()


def test_start_fails(mcp_lockfile, monkeypatch, capsys):
    """MCP server fails to start → exit 1."""
    def fake_which(name):
        return f"/usr/bin/{name}"
    monkeypatch.setattr("agentnode_sdk.cli.mcp_commands.shutil.which", fake_which)
    monkeypatch.setattr("agentnode_sdk.cli.mcp_commands.subprocess.run",
                        lambda *a, **kw: mock.Mock(stdout="v20.0.0\n"))
    monkeypatch.setenv("TEST_API_KEY", "test-key-123")

    mock_server = mock.MagicMock()
    mock_server.start.side_effect = RuntimeError("MCP initialize failed")

    with mock.patch("agentnode_sdk.runtimes.mcp_runner.MCPServerProcess",
                    return_value=mock_server):
        result = cmd_mcp_doctor("mcp-test", json_output=False, skip_start=False)

    assert result == 1
    out = capsys.readouterr().out
    assert "failed" in out.lower()


def test_json_output_pass(mcp_lockfile, monkeypatch, capsys):
    """JSON output with all checks passing."""
    def fake_which(name):
        return f"/usr/bin/{name}"
    monkeypatch.setattr("agentnode_sdk.cli.mcp_commands.shutil.which", fake_which)
    monkeypatch.setattr("agentnode_sdk.cli.mcp_commands.subprocess.run",
                        lambda *a, **kw: mock.Mock(stdout="v20.0.0\n"))
    monkeypatch.setenv("TEST_API_KEY", "test-key-123")

    result = cmd_mcp_doctor("mcp-test", json_output=True, skip_start=True)
    assert result == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["slug"] == "mcp-test"
    assert data["ready"] is True
    assert all(c["ok"] in (True, None) for c in data["checks"])


def test_json_output_fail(monkeypatch, capsys):
    """JSON output with failure."""
    monkeypatch.setattr("agentnode_sdk.cli.mcp_commands.read_lockfile",
                        lambda: {"packages": {}})
    result = cmd_mcp_doctor("missing", json_output=True, skip_start=True)
    assert result == 1
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["ready"] is False


def test_no_env_keys_no_warning(mcp_lockfile, monkeypatch, capsys):
    """MCP with no env_keys → no env check shown, still passes."""
    lock_data = {
        "packages": {
            "mcp-filesystem": {
                "version": "0.1.0",
                "runtime": "mcp",
                "mcp_command": ["npx", "-y", "test-fs@1.0.0"],
                "mcp_env_keys": [],
                "tools": [{"name": "read_file"}],
                "permissions": {},
            },
        },
    }
    monkeypatch.setattr("agentnode_sdk.cli.mcp_commands.read_lockfile", lambda: lock_data)

    def fake_which(name):
        return f"/usr/bin/{name}"
    monkeypatch.setattr("agentnode_sdk.cli.mcp_commands.shutil.which", fake_which)
    monkeypatch.setattr("agentnode_sdk.cli.mcp_commands.subprocess.run",
                        lambda *a, **kw: mock.Mock(stdout="v20.0.0\n"))

    result = cmd_mcp_doctor("mcp-filesystem", json_output=False, skip_start=True)
    assert result == 0
    out = capsys.readouterr().out
    assert "Ready to run" in out
    assert "env" not in out.lower() or "not set" not in out.lower()
