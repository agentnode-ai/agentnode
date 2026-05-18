"""Tests for Phase 8.3: Audit + chain tracing for per-tool overrides."""
from __future__ import annotations

import json

import pytest

from agentnode_sdk.guard import (
    GuardDecision,
    check_action,
    reset_guard_config_cache,
    reset_rate_limits,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _guard_env(tmp_path, monkeypatch):
    cfg = {
        "version": "1",
        "guard": {
            "delete": "prompt",
            "write_external": "prompt",
            "execute": "prompt",
            "credential_use": "prompt",
            "network_egress": "allow",
            "write_local": "allow",
            "read": "allow",
            "compute": "allow",
            "unknown": "prompt",
        },
    }
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(cfg))
    monkeypatch.setenv("AGENTNODE_CONFIG", str(cfg_file))
    monkeypatch.setenv("AGENTNODE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("AGENTNODE_GUARD_STRICT", raising=False)
    reset_guard_config_cache()
    reset_rate_limits()
    yield
    reset_guard_config_cache()
    reset_rate_limits()


def _entry(*, trust="verified", tools=None, connector=None, package_type="toolpack"):
    e = {
        "trust_level": trust,
        "permissions": {
            "network_level": "none",
            "filesystem_level": "none",
            "code_execution_level": "none",
        },
        "package_type": package_type,
    }
    if tools is not None:
        e["tools"] = tools
    if connector is not None:
        e["connector"] = connector
    return e


def _set_tool_overrides(tmp_path, overrides):
    cfg_file = tmp_path / "config.json"
    cfg = json.loads(cfg_file.read_text())
    cfg["guard"]["tool_overrides"] = overrides
    cfg_file.write_text(json.dumps(cfg))
    reset_guard_config_cache()


def _write_audit_and_read(tmp_path, decision, slug, tool_name, entry):
    """Write a guard audit entry and read back the JSONL."""
    from agentnode_sdk.policy import audit_decision, PolicyResult
    audit_decision(
        PolicyResult(
            action=decision.action,
            reason=decision.reason,
            source=decision.source,
        ),
        "guard_check",
        slug,
        tool_name=tool_name,
        trust_level=entry.get("trust_level"),
    )
    audit_path = tmp_path / "audit.jsonl"
    lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines]


# ---------------------------------------------------------------------------
# Audit source field for tool overrides
# ---------------------------------------------------------------------------

class TestAuditSource:
    def test_tool_override_deny_writes_source(self, tmp_path):
        _set_tool_overrides(tmp_path, {
            "pkg/danger": {"delete": "deny"},
        })
        entry = _entry(tools=[{"name": "danger", "action_type": "delete"}])
        decision = check_action("pkg", "danger", {}, entry)
        records = _write_audit_and_read(tmp_path, decision, "pkg", "danger", entry)
        assert len(records) == 1
        assert records[0]["source"] == "guard.tool_override.pkg/danger"
        assert records[0]["action"] == "deny"

    def test_tool_override_prompt_writes_source(self, tmp_path):
        _set_tool_overrides(tmp_path, {
            "pkg/reader": {"read": "prompt"},
        })
        entry = _entry(tools=[{"name": "reader", "action_type": "read"}])
        decision = check_action("pkg", "reader", {}, entry)
        records = _write_audit_and_read(tmp_path, decision, "pkg", "reader", entry)
        assert records[0]["source"] == "guard.tool_override.pkg/reader"
        assert records[0]["action"] == "prompt"

    def test_global_policy_no_tool_override_in_source(self, tmp_path):
        entry = _entry(tools=[{"name": "tool", "action_type": "delete"}])
        decision = check_action("pkg", "tool", {}, entry)
        records = _write_audit_and_read(tmp_path, decision, "pkg", "tool", entry)
        assert "tool_override" not in records[0]["source"]

    def test_audit_tool_name_preserved(self, tmp_path):
        _set_tool_overrides(tmp_path, {
            "pkg/my_tool": {"delete": "deny"},
        })
        entry = _entry(tools=[{"name": "my_tool", "action_type": "delete"}])
        decision = check_action("pkg", "my_tool", {}, entry)
        records = _write_audit_and_read(tmp_path, decision, "pkg", "my_tool", entry)
        assert records[0]["tool_name"] == "my_tool"
        assert records[0]["slug"] == "pkg"


# ---------------------------------------------------------------------------
# Guard chain format verification
# ---------------------------------------------------------------------------

class TestGuardChainFormat:
    def test_deny_chain_format(self, tmp_path):
        _set_tool_overrides(tmp_path, {
            "file-manager/delete_file": {"delete": "deny"},
        })
        entry = _entry(tools=[{"name": "delete_file", "action_type": "delete"}])
        decision = check_action("file-manager", "delete_file", {}, entry)
        assert any(
            c == "guard_action:deny(delete:tool_override[file-manager/delete_file])"
            for c in decision.guard_chain
        )

    def test_prompt_chain_format(self, tmp_path):
        _set_tool_overrides(tmp_path, {
            "pkg/tool": {"read": "prompt"},
        })
        entry = _entry(tools=[{"name": "tool", "action_type": "read"}])
        decision = check_action("pkg", "tool", {}, entry)
        assert any(
            c == "guard_action:prompt(read:tool_override[pkg/tool])"
            for c in decision.guard_chain
        )

    def test_allow_chain_format(self, tmp_path):
        _set_tool_overrides(tmp_path, {
            "pkg/tool": {"delete": "allow"},
        })
        entry = _entry(tools=[{"name": "tool", "action_type": "delete"}])
        decision = check_action("pkg", "tool", {}, entry)
        assert any(
            c == "guard_action:allow(delete:tool_override[pkg/tool])"
            for c in decision.guard_chain
        )

    def test_no_override_chain_has_no_tool_override(self):
        entry = _entry(tools=[{"name": "tool", "action_type": "delete"}])
        decision = check_action("pkg", "tool", {}, entry)
        for c in decision.guard_chain:
            assert "tool_override" not in c


# ---------------------------------------------------------------------------
# Audit filtering by --tool
# ---------------------------------------------------------------------------

class TestAuditToolFiltering:
    def test_audit_filter_finds_tool_override_entry(self, tmp_path):
        from agentnode_sdk.cli.audit import read_audit_entries
        _set_tool_overrides(tmp_path, {
            "pkg/danger": {"delete": "deny"},
        })
        entry = _entry(tools=[{"name": "danger", "action_type": "delete"}])
        decision = check_action("pkg", "danger", {}, entry)
        _write_audit_and_read(tmp_path, decision, "pkg", "danger", entry)

        entries = read_audit_entries(limit=100, tool_name="danger")
        assert len(entries) == 1
        assert entries[0]["tool_name"] == "danger"

    def test_audit_filter_by_slug(self, tmp_path):
        from agentnode_sdk.cli.audit import read_audit_entries
        _set_tool_overrides(tmp_path, {
            "pkg-a/tool": {"delete": "deny"},
        })
        entry = _entry(tools=[{"name": "tool", "action_type": "delete"}])
        decision = check_action("pkg-a", "tool", {}, entry)
        _write_audit_and_read(tmp_path, decision, "pkg-a", "tool", entry)

        entries = read_audit_entries(limit=100, slug="pkg-a")
        assert len(entries) == 1
        assert entries[0]["slug"] == "pkg-a"

    def test_audit_filter_by_action_deny(self, tmp_path):
        from agentnode_sdk.cli.audit import read_audit_entries
        _set_tool_overrides(tmp_path, {
            "pkg/tool": {"delete": "deny"},
        })
        entry = _entry(tools=[{"name": "tool", "action_type": "delete"}])
        decision = check_action("pkg", "tool", {}, entry)
        _write_audit_and_read(tmp_path, decision, "pkg", "tool", entry)

        entries = read_audit_entries(limit=100, action="deny")
        assert len(entries) == 1
        assert entries[0]["action"] == "deny"
