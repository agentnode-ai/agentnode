"""Tests for Phase 5: Observability & Trust Transparency."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    cfg_dir = tmp_path / ".agentnode"
    cfg_dir.mkdir()
    cfg_file = cfg_dir / "config.json"
    lock_file = tmp_path / "agentnode.lock"
    monkeypatch.setenv("AGENTNODE_CONFIG", str(cfg_file))
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(lock_file))
    monkeypatch.delenv("AGENTNODE_NON_INTERACTIVE", raising=False)
    return tmp_path


# --- RunToolResult ---


def test_run_tool_result_to_dict():
    from agentnode_sdk.models import RunToolResult

    r = RunToolResult(
        success=True,
        result={"key": "value"},
        mode_used="direct",
        duration_ms=42.5,
    )
    d = r.to_dict()
    assert d["success"] is True
    assert d["result"] == {"key": "value"}
    assert d["mode_used"] == "direct"
    assert d["duration_ms"] == 42.5
    assert "error" not in d
    assert "run_id" not in d
    assert "policy" not in d


def test_run_tool_result_to_dict_with_policy():
    from agentnode_sdk.models import RunToolResult

    r = RunToolResult(
        success=False,
        error="denied",
        mode_used="policy_denied",
        policy={"action": "deny", "reason": "trust too low", "source": "trust_level"},
    )
    d = r.to_dict()
    assert d["success"] is False
    assert d["error"] == "denied"
    assert d["policy"]["action"] == "deny"
    assert d["policy"]["source"] == "trust_level"


def test_run_tool_result_to_dict_minimal():
    from agentnode_sdk.models import RunToolResult

    r = RunToolResult(success=True)
    d = r.to_dict()
    assert d == {"success": True, "mode_used": "direct", "duration_ms": 0.0, "timed_out": False}


def test_run_tool_result_policy_default_none():
    from agentnode_sdk.models import RunToolResult

    r = RunToolResult(success=True)
    assert r.policy is None


# --- audit command ---


def test_audit_empty(isolated_env, capsys):
    from agentnode_sdk.cli.audit import cmd_audit

    ret = cmd_audit()
    assert ret == 0
    assert "No audit log" in capsys.readouterr().out


def test_audit_reads_entries(isolated_env, capsys):
    from agentnode_sdk.cli.audit import cmd_audit

    cfg_dir = isolated_env / ".agentnode"
    audit_path = cfg_dir / "audit.jsonl"
    entries = [
        {"ts": "2026-05-05T14:00:00", "event": "run_tool", "slug": "web-search", "action": "allow", "source": "default", "reason": "", "trust": "trusted"},
        {"ts": "2026-05-05T14:01:00", "event": "run_tool", "slug": "pdf-reader", "action": "deny", "source": "trust_level", "reason": "trust too low", "trust": "unverified"},
    ]
    audit_path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")

    with patch("agentnode_sdk.config.config_dir", return_value=cfg_dir):
        ret = cmd_audit()

    out = capsys.readouterr().out
    assert ret == 0
    assert "web-search" in out
    assert "pdf-reader" in out
    assert "DENY" in out


def test_audit_json_output(isolated_env, capsys):
    from agentnode_sdk.cli.audit import cmd_audit

    cfg_dir = isolated_env / ".agentnode"
    audit_path = cfg_dir / "audit.jsonl"
    entry = {"ts": "2026-05-05T14:00:00", "event": "run_tool", "slug": "test-pack", "action": "allow", "source": "default", "reason": "", "trust": "trusted", "env": "win32/user", "request_id": None}
    audit_path.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    with patch("agentnode_sdk.config.config_dir", return_value=cfg_dir):
        ret = cmd_audit(json_output=True)

    out = capsys.readouterr().out
    data = json.loads(out)
    assert ret == 0
    assert len(data) == 1
    assert data[0]["slug"] == "test-pack"
    assert "env" not in data[0]
    assert "request_id" not in data[0]


def test_audit_limit(isolated_env, capsys):
    from agentnode_sdk.cli.audit import cmd_audit

    cfg_dir = isolated_env / ".agentnode"
    audit_path = cfg_dir / "audit.jsonl"
    entries = [
        json.dumps({"ts": f"2026-05-05T14:0{i}:00", "event": "run_tool", "slug": f"pack-{i}", "action": "allow", "source": "default", "reason": "", "trust": "trusted"})
        for i in range(10)
    ]
    audit_path.write_text("\n".join(entries) + "\n", encoding="utf-8")

    with patch("agentnode_sdk.config.config_dir", return_value=cfg_dir):
        ret = cmd_audit(limit=3, json_output=True)

    data = json.loads(capsys.readouterr().out)
    assert ret == 0
    assert len(data) == 3
    assert data[0]["slug"] == "pack-7"


# --- cmd_run --explain for slug ---


def test_cmd_run_explain_slug(isolated_env, capsys):
    from agentnode_sdk.models import RunToolResult
    from agentnode_sdk.cli.commands import cmd_run

    mock_result = RunToolResult(
        success=True,
        result={"output": "hello"},
        mode_used="direct",
        duration_ms=15.0,
        policy={"action": "allow", "reason": "trusted publisher", "source": "trust_level"},
    )

    with patch("agentnode_sdk.runner.run_tool", return_value=mock_result):
        ret = cmd_run("test-pack", input_data='{"q":"test"}', explain=True)

    out = capsys.readouterr().out
    assert ret == 0
    assert "allow" in out
    assert "trusted publisher" in out
    assert "trust_level" in out


# --- cmd_run --json ---


def test_cmd_run_json_output(isolated_env, capsys):
    from agentnode_sdk.models import RunToolResult
    from agentnode_sdk.cli.commands import cmd_run

    mock_result = RunToolResult(
        success=True,
        result={"answer": 42},
        mode_used="subprocess",
        duration_ms=100.0,
        policy={"action": "allow", "reason": "", "source": "default"},
    )

    with patch("agentnode_sdk.runner.run_tool", return_value=mock_result):
        ret = cmd_run("test-pack", input_data='{"q":"test"}', json_output=True)

    out = capsys.readouterr().out
    data = json.loads(out)
    assert ret == 0
    assert data["success"] is True
    assert data["result"] == {"answer": 42}
    assert data["policy"]["action"] == "allow"
    assert data["mode_used"] == "subprocess"


def test_cmd_run_raw_vs_json(isolated_env, capsys):
    """--raw outputs only result payload, --json outputs full to_dict()."""
    from agentnode_sdk.models import RunToolResult
    from agentnode_sdk.cli.commands import cmd_run

    mock_result = RunToolResult(
        success=True,
        result={"data": "hello"},
        mode_used="direct",
        policy={"action": "allow", "reason": "", "source": "default"},
    )

    with patch("agentnode_sdk.runner.run_tool", return_value=mock_result):
        cmd_run("test-pack", input_data='{"q":"x"}', raw=True)
    raw_out = json.loads(capsys.readouterr().out)
    assert raw_out == {"data": "hello"}
    assert "policy" not in raw_out
    assert "mode_used" not in raw_out


# --- cmd_doctor --json ---


def test_cmd_doctor_json(isolated_env, capsys):
    from agentnode_sdk.cli.commands import cmd_doctor

    lock_path = isolated_env / "agentnode.lock"
    lock_path.write_text(json.dumps({
        "lockfile_version": "0.1",
        "updated_at": "",
        "packages": {
            "test-pack": {
                "version": "1.0.0",
                "trust_level": "verified",
                "capability_ids": ["web_search"],
            }
        },
    }), encoding="utf-8")

    with patch("agentnode_sdk.cli.commands.config_exists", return_value=True), \
         patch("agentnode_sdk.cli.commands.config_path", return_value=isolated_env / ".agentnode" / "config.json"):
        cfg_path = isolated_env / ".agentnode" / "config.json"
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text("{}", encoding="utf-8")
        ret = cmd_doctor(json_output=True)

    out = capsys.readouterr().out
    data = json.loads(out)
    assert ret == 0
    assert "health" in data
    assert "installed" in data
    assert data["health"]["sdk"]["ok"] is True
    assert "test-pack" in data["installed"]


# --- cmd_logs ---


def test_cmd_logs_empty(isolated_env, capsys):
    from agentnode_sdk.cli.commands import cmd_logs

    with patch("agentnode_sdk.run_log.list_runs", return_value=[]):
        ret = cmd_logs()
    assert ret == 0
    assert "No run logs" in capsys.readouterr().out


def test_cmd_logs_list(isolated_env, capsys):
    from agentnode_sdk.cli.commands import cmd_logs

    mock_events = [
        {"ts": "2026-05-05T14:00:00", "event": "run_start", "slug": "my-agent", "run_id": "abc123"},
        {"ts": "2026-05-05T14:00:05", "event": "run_end", "slug": "my-agent", "run_id": "abc123", "success": True},
    ]
    with patch("agentnode_sdk.run_log.list_runs", return_value=["abc123"]), \
         patch("agentnode_sdk.run_log.read_run", return_value=mock_events):
        ret = cmd_logs()

    out = capsys.readouterr().out
    assert ret == 0
    assert "my-agent" in out


def test_cmd_logs_show_run(isolated_env, capsys):
    from agentnode_sdk.cli.commands import cmd_logs

    mock_events = [
        {"ts": "2026-05-05T14:00:00", "event": "run_start", "slug": "my-agent"},
        {"ts": "2026-05-05T14:00:01", "event": "tool_call", "tool_name": "web_search"},
        {"ts": "2026-05-05T14:00:02", "event": "tool_result", "success": True},
        {"ts": "2026-05-05T14:00:03", "event": "run_end", "slug": "my-agent", "success": True},
    ]
    with patch("agentnode_sdk.run_log.read_run", return_value=mock_events):
        ret = cmd_logs(run_id="abc123")

    out = capsys.readouterr().out
    assert ret == 0
    assert "run_start" in out
    assert "tool_call" in out
    assert "web_search" in out


def test_cmd_logs_json(isolated_env, capsys):
    from agentnode_sdk.cli.commands import cmd_logs

    mock_events = [
        {"ts": "2026-05-05T14:00:00", "event": "run_start", "slug": "test"},
    ]
    with patch("agentnode_sdk.run_log.read_run", return_value=mock_events):
        ret = cmd_logs(run_id="xyz", json_output=True)

    data = json.loads(capsys.readouterr().out)
    assert ret == 0
    assert len(data) == 1
    assert data[0]["event"] == "run_start"


def test_cmd_logs_not_found(isolated_env, capsys):
    from agentnode_sdk.cli.commands import cmd_logs

    with patch("agentnode_sdk.run_log.read_run", return_value=[]):
        ret = cmd_logs(run_id="nonexistent")
    assert ret == 1
    assert "not found" in capsys.readouterr().out
