"""Tests for Phase 6.2b: Audit-Auswertung — filtered audit display."""
from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest

from agentnode_sdk.cli.audit import cmd_audit, read_audit_entries


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SAMPLE_ENTRIES = [
    {
        "ts": "2026-05-13T10:00:00+00:00",
        "event": "run_tool",
        "slug": "csv-pack",
        "tool_name": None,
        "action": "allow",
        "source": "policy.default",
        "reason": "All checks passed",
        "trust": "verified",
        "env": "cli",
    },
    {
        "ts": "2026-05-13T10:00:01+00:00",
        "event": "guard_check",
        "slug": "csv-pack",
        "tool_name": None,
        "action": "allow",
        "source": "guard.default",
        "reason": "All action types allowed",
        "trust": "verified",
        "env": "cli",
    },
    {
        "ts": "2026-05-13T10:01:00+00:00",
        "event": "run_tool",
        "slug": "del-pack",
        "tool_name": "delete_all",
        "action": "allow",
        "source": "policy.default",
        "reason": "All checks passed",
        "trust": "trusted",
        "env": "cli",
    },
    {
        "ts": "2026-05-13T10:01:01+00:00",
        "event": "guard_check",
        "slug": "del-pack",
        "tool_name": "delete_all",
        "action": "deny",
        "source": "guard.action_policy.delete",
        "reason": "Action type 'delete' denied by guard policy",
        "trust": "trusted",
        "env": "cli",
    },
    {
        "ts": "2026-05-13T10:02:00+00:00",
        "event": "guard_confirmation",
        "slug": "exec-pack",
        "tool_name": "run_cmd",
        "action": "allow",
        "source": "guard.confirmation",
        "reason": "User confirmed execution",
        "trust": "verified",
        "env": "cli",
    },
    {
        "ts": "2026-05-13T10:03:00+00:00",
        "event": "guard_rate_limit",
        "slug": "spam-pack",
        "tool_name": None,
        "action": "deny",
        "source": "guard.rate_limit",
        "reason": "Rate limit exceeded: 61 calls/min (limit 60)",
        "trust": "unverified",
        "env": "cli",
    },
    {
        "ts": "2026-05-13T10:04:00+00:00",
        "event": "guard_check",
        "slug": "send-pack",
        "tool_name": "send_email",
        "action": "prompt",
        "source": "guard.action_policy.write_external",
        "reason": "Action type 'write_external' requires confirmation",
        "trust": "verified",
        "env": "cli",
    },
]


@pytest.fixture
def audit_dir(tmp_path, monkeypatch):
    """Write sample audit entries and point config_dir at tmp_path."""
    audit_path = tmp_path / "audit.jsonl"
    lines = [json.dumps(e) for e in _SAMPLE_ENTRIES]
    audit_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "config.json"))
    return tmp_path


# ---------------------------------------------------------------------------
# read_audit_entries — filter tests
# ---------------------------------------------------------------------------

class TestReadAuditEntriesFilters:
    def test_no_filter_returns_all(self, audit_dir):
        entries = read_audit_entries()
        assert len(entries) == 7

    def test_filter_by_event(self, audit_dir):
        entries = read_audit_entries(event="guard_check")
        assert len(entries) == 3
        assert all(e["event"] == "guard_check" for e in entries)

    def test_filter_by_slug(self, audit_dir):
        entries = read_audit_entries(slug="del-pack")
        assert len(entries) == 2
        slugs = {e["slug"] for e in entries}
        assert slugs == {"del-pack"}

    def test_filter_by_action(self, audit_dir):
        entries = read_audit_entries(action="deny")
        assert len(entries) == 2
        assert all(e["action"] == "deny" for e in entries)

    def test_filter_by_action_case_insensitive(self, audit_dir):
        entries = read_audit_entries(action="DENY")
        assert len(entries) == 2

    def test_filter_by_tool_name(self, audit_dir):
        entries = read_audit_entries(tool_name="delete_all")
        assert len(entries) == 2
        assert all(e["tool_name"] == "delete_all" for e in entries)

    def test_combined_filters(self, audit_dir):
        entries = read_audit_entries(event="guard_check", action="deny")
        assert len(entries) == 1
        assert entries[0]["slug"] == "del-pack"

    def test_filter_with_limit(self, audit_dir):
        entries = read_audit_entries(event="guard_check", limit=2)
        assert len(entries) == 2
        # Should be the last 2 guard_check entries
        assert entries[-1]["slug"] == "send-pack"

    def test_filter_no_match(self, audit_dir):
        entries = read_audit_entries(slug="nonexistent-pack")
        assert len(entries) == 0

    def test_limit_applied_after_filter(self, audit_dir):
        all_guard = read_audit_entries(event="guard_check")
        assert len(all_guard) == 3
        limited = read_audit_entries(event="guard_check", limit=1)
        assert len(limited) == 1
        # Last entry from guard_check set
        assert limited[0] == all_guard[-1]


# ---------------------------------------------------------------------------
# read_audit_entries — edge cases
# ---------------------------------------------------------------------------

class TestReadAuditEntriesEdgeCases:
    def test_empty_file(self, tmp_path, monkeypatch):
        audit_path = tmp_path / "audit.jsonl"
        audit_path.write_text("", encoding="utf-8")
        monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "config.json"))
        assert read_audit_entries() == []

    def test_no_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "config.json"))
        assert read_audit_entries() == []

    def test_malformed_lines_skipped(self, tmp_path, monkeypatch):
        audit_path = tmp_path / "audit.jsonl"
        audit_path.write_text(
            '{"event":"run_tool","slug":"ok","action":"allow"}\n'
            'not json\n'
            '{"event":"guard_check","slug":"ok2","action":"deny"}\n',
            encoding="utf-8",
        )
        monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "config.json"))
        entries = read_audit_entries()
        assert len(entries) == 2

    def test_sanitization_strips_extra_fields(self, tmp_path, monkeypatch):
        audit_path = tmp_path / "audit.jsonl"
        audit_path.write_text(
            json.dumps({
                "event": "run_tool", "slug": "p", "action": "allow",
                "env": "cli", "request_id": "abc-123", "secret_data": "SHOULD_NOT_APPEAR",
            }) + "\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "config.json"))
        entries = read_audit_entries()
        assert len(entries) == 1
        assert "secret_data" not in entries[0]
        assert "request_id" not in entries[0]
        assert "env" not in entries[0]


# ---------------------------------------------------------------------------
# cmd_audit — table display
# ---------------------------------------------------------------------------

class TestCmdAuditDisplay:
    def test_table_shows_event_type(self, audit_dir, capsys):
        cmd_audit(limit=10)
        out = capsys.readouterr().out
        assert "guard_check" in out
        assert "run_tool" in out

    def test_table_shows_slug_tool(self, audit_dir, capsys):
        cmd_audit(limit=10)
        out = capsys.readouterr().out
        assert "del-pack::delete_all" in out

    def test_table_shows_slug_only_when_no_tool(self, audit_dir, capsys):
        cmd_audit(limit=10)
        out = capsys.readouterr().out
        # csv-pack has no tool_name, should NOT show ::
        lines = [l for l in out.splitlines() if "csv-pack" in l]
        assert any("csv-pack" in l and "::" not in l for l in lines)

    def test_deny_shows_reason_and_source(self, audit_dir, capsys):
        cmd_audit(limit=10)
        out = capsys.readouterr().out
        assert "denied by guard policy" in out
        assert "guard.action_policy.delete" in out

    def test_prompt_shows_reason(self, audit_dir, capsys):
        cmd_audit(limit=10)
        out = capsys.readouterr().out
        assert "requires confirmation" in out

    def test_guard_confirmation_shows_reason(self, audit_dir, capsys):
        cmd_audit(limit=10)
        out = capsys.readouterr().out
        assert "User confirmed execution" in out

    def test_empty_log_message(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "config.json"))
        cmd_audit()
        out = capsys.readouterr().out
        assert "No audit log found" in out

    def test_no_matching_entries(self, audit_dir, capsys):
        cmd_audit(slug="nonexistent")
        out = capsys.readouterr().out
        assert "No matching" in out


# ---------------------------------------------------------------------------
# cmd_audit — JSON output respects filters
# ---------------------------------------------------------------------------

class TestCmdAuditJsonFiltered:
    def test_json_unfiltered(self, audit_dir, capsys):
        cmd_audit(limit=100, json_output=True)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert len(data) == 7

    def test_json_filter_event(self, audit_dir, capsys):
        cmd_audit(limit=100, json_output=True, event="guard_check")
        out = capsys.readouterr().out
        data = json.loads(out)
        assert len(data) == 3
        assert all(e["event"] == "guard_check" for e in data)

    def test_json_filter_slug(self, audit_dir, capsys):
        cmd_audit(limit=100, json_output=True, slug="del-pack")
        out = capsys.readouterr().out
        data = json.loads(out)
        assert len(data) == 2

    def test_json_filter_action(self, audit_dir, capsys):
        cmd_audit(limit=100, json_output=True, action="deny")
        out = capsys.readouterr().out
        data = json.loads(out)
        assert len(data) == 2

    def test_json_filter_tool(self, audit_dir, capsys):
        cmd_audit(limit=100, json_output=True, tool_name="run_cmd")
        out = capsys.readouterr().out
        data = json.loads(out)
        assert len(data) == 1
        assert data[0]["event"] == "guard_confirmation"

    def test_json_combined_filters(self, audit_dir, capsys):
        cmd_audit(limit=100, json_output=True, event="guard_check", action="deny")
        out = capsys.readouterr().out
        data = json.loads(out)
        assert len(data) == 1
        assert data[0]["slug"] == "del-pack"


# ---------------------------------------------------------------------------
# cmd_audit — filter flags
# ---------------------------------------------------------------------------

class TestCmdAuditFilterFlags:
    def test_event_filter(self, audit_dir, capsys):
        cmd_audit(event="guard_confirmation")
        out = capsys.readouterr().out
        assert "exec-pack::run_cmd" in out
        assert "del-pack" not in out

    def test_slug_filter(self, audit_dir, capsys):
        cmd_audit(slug="spam-pack")
        out = capsys.readouterr().out
        assert "spam-pack" in out
        assert "csv-pack" not in out

    def test_action_filter(self, audit_dir, capsys):
        cmd_audit(action="prompt")
        out = capsys.readouterr().out
        assert "send-pack" in out
        assert "csv-pack" not in out


# ---------------------------------------------------------------------------
# Rate limit event type (bug fix verification)
# ---------------------------------------------------------------------------

class TestRateLimitEventType:
    def test_rate_limit_entry_has_correct_event(self, audit_dir):
        entries = read_audit_entries(event="guard_rate_limit")
        assert len(entries) == 1
        assert entries[0]["slug"] == "spam-pack"
        assert entries[0]["action"] == "deny"

    def test_rate_limit_not_in_guard_check(self, audit_dir):
        guard_checks = read_audit_entries(event="guard_check")
        slugs = [e["slug"] for e in guard_checks]
        assert "spam-pack" not in slugs
